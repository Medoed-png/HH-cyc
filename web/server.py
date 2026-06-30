"""Веб-интерфейс HH-бота на FastAPI (ASGI) поверх Python-бэкенда.

Порт с Flask на FastAPI/uvicorn (M3a): те же эндпоинты, статика и поток событий
(SSE). ASGI выбран ради аутентификации/скоупинга через зависимости (M3b). Логин
на hh.ru и парсинг — через per-user сессии браузера в пуле SessionManager (M4).
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
import webbrowser

from contextlib import asynccontextmanager

import uvicorn
from sqlalchemy.exc import IntegrityError
from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hh_bot import config as config_mod
from hh_bot import credentials as creds_mod
from hh_bot.cities_list import CITIES
from hh_bot.db import User
from hh_bot.suggest import fetch_suggestions
from runtime import SessionManager
from sites import list_sites, get_adapter, DEFAULT_SITE, ALL_SITES, real_site_ids
from web import auth
from web.auth import current_user, _extract_token, _user_id_from_token

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Крупные города — всегда выше в подсказках, даже если название длинное.
_MAJOR_CITIES = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "краснодар", "воронеж", "пермь", "волгоград", "россия",
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # При старте: создать схему БД (идемпотентно) до обслуживания запросов, иначе
    # эндпоинты подключения аккаунта могут обратиться к таблице раньше, чем её
    # создаст ленивая инициализация в Storage (первая сессия).
    from hh_bot.db import init_db
    init_db()
    # Запомнить event loop сервера: в него потоки сессий будут безопасно класть
    # события для SSE-очередей (call_soon_threadsafe).
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    # Автопилот: периодический поиск+отклик для пользователей, включивших его.
    from runtime.scheduler import Autopilot
    autopilot = Autopilot(manager)
    autopilot.start()
    yield
    # При остановке: корректно закрыть все сессии, чтобы сохранились cookies (вход).
    autopilot.stop()
    manager.shutdown_all()


app = FastAPI(title="HH-бот", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """Приводим 422 Pydantic к единому формату {error}, который читает фронтенд
    (раньше клиент читал data.error, а сервер слал data.detail → сообщение терялось)."""
    field = ""
    try:
        field = (exc.errors()[0].get("loc") or [None])[-1]
    except Exception:  # noqa: BLE001
        pass
    msg = ("Неверный формат email." if field == "email"
           else "Пароль слишком короткий (минимум 6 символов)." if field == "password"
           else "Проверьте введённые данные.")
    return JSONResponse({"error": msg}, status_code=422)


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Не кэшировать HTML/JS/CSS, чтобы браузер всегда брал свежую версию интерфейса
    (иначе после обновлений фронтенда остаётся старый app.js и кнопки «не работают»)."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path == "/login" or path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# --- per-user SSE: реестр подписчиков по user_id ---
# Каждое открытое соединение /api/events регистрирует свою asyncio.Queue; события
# сессии пользователя рассылаются только в его очереди (изоляция между людьми).
# Очереди асинхронные (SSE-эндпоинт async): соединение больше НЕ занимает поток
# из anyio-пула на всё время жизни (раньше десятки вкладок исчерпывали пул).
_subscribers: dict[int, set] = {}
_subs_lock = threading.Lock()
# Ссылка на event loop uvicorn — события приходят из ПОТОКОВ сессий, а asyncio.Queue
# не потокобезопасна, поэтому put планируем через call_soon_threadsafe.
_event_loop: asyncio.AbstractEventLoop | None = None


def _add_subscriber(user_id: int, q: asyncio.Queue) -> None:
    with _subs_lock:
        _subscribers.setdefault(user_id, set()).add(q)


def _remove_subscriber(user_id: int, q: asyncio.Queue) -> None:
    with _subs_lock:
        subs = _subscribers.get(user_id)
        if subs:
            subs.discard(q)
            if not subs:
                _subscribers.pop(user_id, None)


def _publish(user_id: int, msg: dict) -> None:
    """Разослать событие во все SSE-очереди пользователя (вызывается из потоков сессий).

    asyncio.Queue.put_nowait безопасно дёргать только в loop-потоке, поэтому
    планируем его через call_soon_threadsafe на event loop сервера.
    """
    loop = _event_loop
    if loop is None:
        return
    with _subs_lock:
        targets = list(_subscribers.get(user_id, ()))
    for q in targets:
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except RuntimeError:  # noqa: BLE001 — loop останавливается
            pass


# --- одноразовые билеты для SSE ---
# EventSource не умеет слать заголовок Authorization, а долгоживущий JWT в URL
# (?token=) утекает в логи/историю. Поэтому фронт берёт КРАТКОЖИВУЩИЙ ОДНОРАЗОВЫЙ
# билет (POST с токеном в заголовке) и подключается к SSE по нему.
_tickets: dict[str, tuple[int, float]] = {}  # ticket -> (user_id, expires_at monotonic)
_tickets_lock = threading.Lock()
_TICKET_TTL = 30.0  # секунд — билет нужен лишь на момент открытия соединения


def _issue_ticket(user_id: int) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _tickets_lock:
        for k in [k for k, (_uid, exp) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)  # подчистить протухшие, чтобы словарь не рос
        _tickets[ticket] = (user_id, now + _TICKET_TTL)
    return ticket


def _consume_ticket(ticket: str) -> int | None:
    """Проверить и СРАЗУ погасить билет (одноразовый). Вернуть user_id или None."""
    with _tickets_lock:
        item = _tickets.pop(ticket, None)
    if not item:
        return None
    user_id, exp = item
    return user_id if exp >= time.monotonic() else None


# Пул per-user сессий браузера (владеет Playwright; события -> _publish).
manager = SessionManager(_publish)


# ---------- статика ----------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/login")
def login_page():
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


# ---------- аутентификация ----------
@app.post("/auth/register")
def auth_register(data: auth.RegisterIn):
    if auth.get_user_by_email(data.email):
        return JSONResponse({"error": "Пользователь с таким email уже есть"},
                            status_code=409)
    try:
        user = auth.create_user(data.email, data.password)
    except IntegrityError:
        # Гонка: email заняли между проверкой и вставкой. Уникальный индекс —
        # источник истины; отдаём тот же 409, а не необработанный 500.
        return JSONResponse({"error": "Пользователь с таким email уже есть"},
                            status_code=409)
    return {"token": auth.create_access_token(user.id),
            "user": {"id": user.id, "email": user.email}}


@app.post("/auth/login")
def auth_login(data: auth.LoginIn):
    user = auth.get_user_by_email(data.email)
    if user is None or not auth.verify_password(user.password_hash, data.password):
        return JSONResponse({"error": "Неверный email или пароль"}, status_code=401)
    return {"token": auth.create_access_token(user.id),
            "user": {"id": user.id, "email": user.email}}


@app.post("/auth/logout")
def auth_logout(user: User = Depends(current_user)):
    # Stateless-токен: клиент просто забывает его. Отзыв сессий — позже.
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}


async def _body(request: Request) -> dict:
    """Тело JSON-запроса (или {} если тела нет/оно не JSON)."""
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 — пустое/не-JSON тело
        return {}


def _site(data: dict | None = None, request: Request | None = None) -> str:
    """Выбранный сайт: из тела (POST) или query ?site= (GET); иначе сайт по умолчанию."""
    if data and data.get("site"):
        return str(data["site"])
    if request is not None:
        q = request.query_params.get("site")
        if q:
            return q
    return DEFAULT_SITE


# ---------- API (требуют входа) ----------
@app.get("/api/sites")
def api_sites(user: User = Depends(current_user)):
    """Список доступных сайтов для выпадающего списка в UI."""
    return list_sites()


@app.get("/api/login_methods")
def api_login_methods(request: Request, user: User = Depends(current_user)):
    """Способы входа выбранного сайта (драйвит UI подключения аккаунта).

    Источник истины — adapter.login_methods(). Возвращает [{id,label,fields,hint}].
    """
    from dataclasses import asdict
    site = _site(request=request)
    if site == ALL_SITES:
        return []
    try:
        adapter = get_adapter(site)
    except KeyError:
        return []
    return [asdict(m) for m in adapter.login_methods()]


@app.get("/api/config")
def api_config(request: Request, user: User = Depends(current_user)):
    """Текущие критерии пользователя для заполнения формы."""
    crit = config_mod.load_for(user.id, _site(request=request))
    region_name = {v: k for k, v in CITIES.items()}.get(str(crit.region), "Россия")
    return {
        "professions": ", ".join(crit.profession_texts),
        "region": region_name,
        # 0 = без фильтра — отдаём пустым, чтобы поле показывало пример, а не «0».
        "salary_min": crit.salary_min or "",
        "exclude_words": ", ".join(crit.exclude_words),
        "include_words": ", ".join(crit.include_words),
        "resume_name": crit.resume_name,
        "cover_letter": crit.cover_letter,
        "auto_letter": crit.auto_letter,
        "daily_limit": crit.daily_limit,
        "max_pages": crit.max_pages,
        "all_pages": crit.all_pages,
        # Фильтры поиска: experience — строка, employment/schedule — списки кодов,
        # company_blacklist — строка через запятую (как exclude_words).
        "experience": crit.experience,
        "employment": crit.employment,
        "schedule": crit.schedule,
        "company_blacklist": ", ".join(crit.company_blacklist),
        "strict_title_match": crit.strict_title_match,
        "autopilot_enabled": crit.autopilot_enabled,
        "autopilot_interval_minutes": crit.autopilot_interval_minutes,
    }


@app.get("/api/stats")
def api_stats(request: Request, user: User = Depends(current_user)):
    """Агрегаты для дашборда статистики (по выбранному сайту)."""
    from hh_bot.storage import Storage
    return Storage(user_id=user.id, site_id=_site(request=request)).stats()


@app.post("/api/save")
async def api_save(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    crit = config_mod.from_form(data)
    config_mod.save_for(user.id, crit, _site(data))
    return {"ok": True}


def _require_site(site: str):
    """Вернуть JSONResponse-ошибку, если выбран режим «все сайты» (нужен конкретный)."""
    if site == ALL_SITES:
        return JSONResponse({"error": "Выберите конкретный сайт"}, status_code=400)
    return None


@app.post("/api/check_login")
async def api_check_login(request: Request, user: User = Depends(current_user)):
    site = _site(await _body(request))
    if site == ALL_SITES:
        return {"ok": True}  # в режиме «все сайты» единый статус входа не нужен
    manager.submit(user.id, site, "check_login")
    return {"ok": True}


@app.post("/api/show_browser")
async def api_show_browser(request: Request, user: User = Depends(current_user)):
    """Показать видимое окно браузера (для капчи/ручных действий)."""
    site = _site(await _body(request))
    if (err := _require_site(site)):
        return err
    manager.submit(user.id, site, "show_browser")
    return {"ok": True}


@app.post("/api/logout_site")
async def api_logout_site(request: Request, user: User = Depends(current_user)):
    """Выйти из аккаунта сайта в сессии (сбросить cookies) — для смены аккаунта."""
    site = _site(await _body(request))
    if (err := _require_site(site)):
        return err
    manager.submit(user.id, site, "logout_site")
    return {"ok": True}


# ---------- подключение аккаунта сайта (серверный логин, M5) ----------
@app.get("/api/conn_status")
def api_conn_status(request: Request, user: User = Depends(current_user)):
    """Статус подключения аккаунта выбранного сайта (без пароля)."""
    return creds_mod.status(user.id, _site(request=request))


@app.post("/api/connect")
async def api_connect(request: Request, user: User = Depends(current_user)):
    """Сохранить (зашифровав) логин/пароль и запустить серверный вход."""
    data = await request.json()
    site = _site(data)
    if (err := _require_site(site)):
        return err
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    # Пароль необязателен: без него вход идёт по коду из SMS/письма.
    if not username:
        return JSONResponse({"error": "Укажите логин (email или телефон)"},
                            status_code=400)
    creds_mod.store(user.id, site, username, password, status=creds_mod.STATUS_INVALID)
    manager.submit(user.id, site, "connect")
    return {"ok": True}


@app.post("/api/sms")
async def api_sms(request: Request, user: User = Depends(current_user)):
    """Передать код подтверждения (SMS/письмо) для завершения входа."""
    data = await request.json()
    site = _site(data)
    if (err := _require_site(site)):
        return err
    code = str(data.get("code", "")).strip()
    if not code:
        return JSONResponse({"error": "Введите код"}, status_code=400)
    manager.submit(user.id, site, "submit_sms", code=code)
    return {"ok": True}


@app.post("/api/disconnect")
async def api_disconnect(request: Request, user: User = Depends(current_user)):
    """Отключить аккаунт: удалить креды И разлогинить браузерную сессию.

    Раньше удалялись только логин/пароль, но cookies/профиль оставались — сессия
    продолжала быть авторизованной (бот работал под «отключённым» аккаунтом).
    """
    site = _site(await _body(request))
    creds_mod.delete(user.id, site)
    if site != ALL_SITES:
        manager.submit(user.id, site, "logout_site")  # стереть cookies/профиль и выйти
    return {"ok": True}


# ---------- прокси пользователя (per-user, анти-бан) ----------
@app.get("/api/proxy")
def api_proxy_status(user: User = Depends(current_user)):
    """Задан ли прокси (без логина/пароля)."""
    return creds_mod.proxy_status(user.id)


@app.post("/api/proxy")
async def api_set_proxy(request: Request, user: User = Depends(current_user)):
    """Сохранить (или очистить пустой строкой) прокси пользователя."""
    proxy_url = str((await request.json()).get("proxy_url", "")).strip()
    creds_mod.set_proxy(user.id, proxy_url)
    return {"ok": True}


# ---------- Telegram-уведомления (per-user) ----------
@app.get("/api/telegram")
def api_telegram_status(user: User = Depends(current_user)):
    """Задан ли chat_id и настроен ли бот на сервере (есть токен)."""
    return creds_mod.telegram_status(user.id)


@app.post("/api/telegram")
async def api_set_telegram(request: Request, user: User = Depends(current_user)):
    """Сохранить/очистить chat_id; при наличии — отправить тестовое сообщение."""
    from hh_bot import notify
    chat_id = str((await request.json()).get("chat_id", "")).strip()
    creds_mod.set_telegram(user.id, chat_id)
    # send_telegram делает сетевой urlopen (до ~10с) — выносим в threadpool, чтобы
    # не блокировать event loop (иначе на это время «висит» весь сервер).
    sent = await run_in_threadpool(
        notify.send_telegram, chat_id, "✅ HH-бот: уведомления подключены."
    ) if chat_id else False
    return {"ok": True, "test_sent": sent}


def _targets(site: str) -> list[str]:
    """Сайты-получатели команды: один сайт или все реальные при site='all'."""
    return real_site_ids() if site == ALL_SITES else [site]


@app.post("/api/search")
async def api_search(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    crit = config_mod.from_form(data)
    for sid in _targets(_site(data)):  # 'all' -> веер по всем сайтам
        manager.submit(user.id, sid, "search", crit=crit)
    return {"ok": True}


@app.post("/api/apply")
async def api_apply(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    crit = config_mod.from_form(data)
    for sid in _targets(_site(data)):
        manager.submit(user.id, sid, "apply", crit=crit)
    return {"ok": True}


@app.post("/api/stop")
async def api_stop(request: Request, user: User = Depends(current_user)):
    # Веер по всем сайтам в режиме «все» (как search/apply) — иначе сессии под
    # ключом (user,'all') нет и стоп молча ничего не останавливает.
    for sid in _targets(_site(await _body(request))):
        manager.request_stop_apply(user.id, sid)
    return {"ok": True}


@app.post("/api/responses")
async def api_responses(request: Request, user: User = Depends(current_user)):
    # Ответы — строго по одному сайту: в режиме «все» события разных площадок
    # затирали бы друг друга в одной таблице (чат/«просмотрено» тоже per-site).
    site = _site(await _body(request))
    if (err := _require_site(site)):
        return err
    manager.submit(user.id, site, "responses")
    return {"ok": True}


@app.post("/api/chat")
async def api_chat(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    vacancy_id = str(data.get("vacancy_id", ""))
    manager.submit(user.id, _site(data), "chat", vacancy_id=vacancy_id)
    return {"ok": True}


@app.get("/api/suggest")
def api_suggest(text: str = "", site: str = DEFAULT_SITE,
                user: User = Depends(current_user)):
    """Подсказки профессий выбранного сайта (через адаптер)."""
    try:
        return get_adapter(site).suggest_professions(text)
    except KeyError:
        return fetch_suggestions(text)


@app.get("/api/cities")
def api_cities(q: str = "", site: str = DEFAULT_SITE,
               user: User = Depends(current_user)):
    """Подсказки городов выбранного сайта (через адаптер)."""
    try:
        return get_adapter(site).suggest_cities(q)
    except KeyError:
        return []


@app.post("/api/events/ticket")
def api_events_ticket(user: User = Depends(current_user)):
    """Выдать одноразовый краткоживущий билет для подключения к SSE.

    Авторизация — по заголовку (current_user); билет затем кладётся в ?ticket=,
    чтобы не светить долгоживущий JWT в URL.
    """
    return {"ticket": _issue_ticket(user.id)}


@app.get("/api/events")
async def api_events(request: Request):
    """Поток событий (SSE) ТОЛЬКО для текущего пользователя.

    Пользователь определяется по ОДНОРАЗОВОМУ билету (?ticket=, /api/events/ticket);
    как фолбэк ещё принимается токен (заголовок/?token=) для совместимости.
    Регистрируем личную asyncio-очередь подписчика; события чужих сессий сюда не
    попадут. Эндпоинт ПОЛНОСТЬЮ асинхронный — соединение не занимает поток из
    anyio-пула на всё своё время (раньше много вкладок исчерпывали пул).
    """
    ticket = request.query_params.get("ticket")
    user_id = _consume_ticket(ticket) if ticket else None
    if user_id is None:  # фолбэк на токен (заголовок Authorization или ?token=)
        tok = _extract_token(request)
        user_id = _user_id_from_token(tok) if tok else None
    if user_id is None:
        return JSONResponse({"error": "Требуется вход"}, status_code=401)

    q: asyncio.Queue = asyncio.Queue()
    _add_subscriber(user_id, q)

    async def stream():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"  # heartbeat, чтобы соединение не падало
        finally:
            _remove_subscriber(user_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _open_browser_when_ready(url: str) -> None:
    """Открыть браузер ТОЛЬКО когда сервер реально начал отвечать.

    Иначе автооткрытая вкладка успевает попасть в окно до того, как uvicorn занял
    порт, и показывает «не удаётся установить соединение». Поллим, пока сервер не
    ответит (до ~30 c), затем открываем.
    """
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:  # noqa: BLE001 — сервер ещё поднимается
            time.sleep(0.5)
    webbrowser.open(url)


def main() -> None:
    url = "http://127.0.0.1:8000"
    print(f"HH-бот: откройте {url}")
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
