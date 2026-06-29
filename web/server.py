"""Веб-интерфейс HH-бота на FastAPI (ASGI) поверх Python-бэкенда.

Порт с Flask на FastAPI/uvicorn (M3a): те же эндпоинты, статика и поток событий
(SSE). ASGI выбран ради аутентификации/скоупинга через зависимости (M3b). Логин
на hh.ru и парсинг — через per-user сессии браузера в пуле SessionManager (M4).
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import webbrowser

from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hh_bot import config as config_mod
from hh_bot import credentials as creds_mod
from hh_bot.cities_list import CITIES
from hh_bot.db import User
from hh_bot.suggest import fetch_suggestions
from runtime import SessionManager
from sites import list_sites, get_adapter, DEFAULT_SITE
from web import auth
from web.auth import current_user

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
    yield
    # При остановке: корректно закрыть все сессии, чтобы сохранились cookies (вход).
    manager.shutdown_all()


app = FastAPI(title="HH-бот", lifespan=lifespan)

# --- per-user SSE: реестр подписчиков по user_id ---
# Каждое открытое соединение /api/events регистрирует свою очередь; события
# сессии пользователя рассылаются только в его очереди (изоляция между людьми).
_subscribers: dict[int, set[queue.Queue]] = {}
_subs_lock = threading.Lock()


def _add_subscriber(user_id: int, q: queue.Queue) -> None:
    with _subs_lock:
        _subscribers.setdefault(user_id, set()).add(q)


def _remove_subscriber(user_id: int, q: queue.Queue) -> None:
    with _subs_lock:
        subs = _subscribers.get(user_id)
        if subs:
            subs.discard(q)
            if not subs:
                _subscribers.pop(user_id, None)


def _publish(user_id: int, msg: dict) -> None:
    """Разослать событие во все SSE-очереди этого пользователя."""
    with _subs_lock:
        targets = list(_subscribers.get(user_id, ()))
    for q in targets:
        q.put(msg)


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
    user = auth.create_user(data.email, data.password)
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


@app.get("/api/config")
def api_config(request: Request, user: User = Depends(current_user)):
    """Текущие критерии пользователя для заполнения формы."""
    crit = config_mod.load_for(user.id, _site(request=request))
    region_name = {v: k for k, v in CITIES.items()}.get(str(crit.region), "Россия")
    return {
        "professions": ", ".join(crit.profession_texts),
        "region": region_name,
        "salary_min": crit.salary_min,
        "exclude_words": ", ".join(crit.exclude_words),
        "include_words": ", ".join(crit.include_words),
        "resume_name": crit.resume_name,
        "cover_letter": crit.cover_letter,
        "daily_limit": crit.daily_limit,
        "max_pages": crit.max_pages,
        # Фильтры поиска: experience — строка, employment/schedule — списки кодов,
        # company_blacklist — строка через запятую (как exclude_words).
        "experience": crit.experience,
        "employment": crit.employment,
        "schedule": crit.schedule,
        "company_blacklist": ", ".join(crit.company_blacklist),
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


@app.post("/api/login")
async def api_login(request: Request, user: User = Depends(current_user)):
    manager.submit(user.id, _site(await _body(request)), "login")
    return {"ok": True}


@app.post("/api/check_login")
async def api_check_login(request: Request, user: User = Depends(current_user)):
    manager.submit(user.id, _site(await _body(request)), "check_login")
    return {"ok": True}


@app.post("/api/show_browser")
async def api_show_browser(request: Request, user: User = Depends(current_user)):
    """Показать видимое окно браузера (для капчи/ручных действий)."""
    manager.submit(user.id, _site(await _body(request)), "show_browser")
    return {"ok": True}


@app.post("/api/logout_site")
async def api_logout_site(request: Request, user: User = Depends(current_user)):
    """Выйти из аккаунта сайта в сессии (сбросить cookies) — для смены аккаунта."""
    manager.submit(user.id, _site(await _body(request)), "logout_site")
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
    code = str(data.get("code", "")).strip()
    if not code:
        return JSONResponse({"error": "Введите код"}, status_code=400)
    manager.submit(user.id, _site(data), "submit_sms", code=code)
    return {"ok": True}


@app.post("/api/disconnect")
async def api_disconnect(request: Request, user: User = Depends(current_user)):
    """Удалить сохранённые креды (отключить аккаунт)."""
    creds_mod.delete(user.id, _site(await _body(request)))
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


@app.post("/api/search")
async def api_search(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    crit = config_mod.from_form(data)
    manager.submit(user.id, _site(data), "search", crit=crit)
    return {"ok": True}


@app.post("/api/apply")
async def api_apply(request: Request, user: User = Depends(current_user)):
    data = await request.json()
    crit = config_mod.from_form(data)
    manager.submit(user.id, _site(data), "apply", crit=crit)
    return {"ok": True}


@app.post("/api/stop")
async def api_stop(request: Request, user: User = Depends(current_user)):
    manager.request_stop_apply(user.id, _site(await _body(request)))
    return {"ok": True}


@app.post("/api/responses")
async def api_responses(request: Request, user: User = Depends(current_user)):
    manager.submit(user.id, _site(await _body(request)), "responses")
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


@app.get("/api/events")
def api_events(user: User = Depends(current_user)):
    """Поток событий (SSE) ТОЛЬКО для текущего пользователя.

    Токен приходит в query (?token=), т.к. EventSource не умеет заголовки.
    Регистрируем личную очередь подписчика; события чужих сессий сюда не попадут.
    Синхронный генератор Starlette крутит в пуле потоков.
    """
    q: queue.Queue = queue.Queue()
    _add_subscriber(user.id, q)

    def stream():
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"  # heartbeat, чтобы соединение не падало
        finally:
            _remove_subscriber(user.id, q)

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
