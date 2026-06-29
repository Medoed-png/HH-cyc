"""Веб-интерфейс HH-бота на FastAPI (ASGI) поверх Python-бэкенда.

Порт с Flask на FastAPI/uvicorn (M3a): поведение прежнее — те же эндпоинты,
статика и поток событий (SSE). ASGI выбран ради аутентификации/скоупинга через
зависимости (M3b) и масштабируемого SSE в облаке (M4). Логин на hh.ru и парсинг —
как и раньше, через рабочий поток Worker (Playwright).
"""
from __future__ import annotations

import json
import os
import queue
import threading
import webbrowser

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hh_bot import config as config_mod
from hh_bot.cities_list import CITIES
from hh_bot.db import User
from hh_bot.suggest import fetch_suggestions
from hh_bot.worker import (Worker, EV_LOG, EV_LOGIN, EV_VACANCY, EV_RESPONSES, EV_CHAT)
from web import auth
from web.auth import current_user

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Крупные города — всегда выше в подсказках, даже если название длинное.
_MAJOR_CITIES = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "краснодар", "воронеж", "пермь", "волгоград", "россия",
}

app = FastAPI(title="HH-бот")

# Один рабочий поток на всё приложение (владеет браузером Playwright).
# Несколько пользователей и пул сессий появятся в M4.
worker = Worker()
worker.start()

# Очередь для SSE: события воркера дублируем сюда уже в JSON-виде.
_sse_queue: queue.Queue = queue.Queue()


def _pump_events() -> None:
    """Перекладывать события воркера в SSE-очередь в виде JSON-сообщений."""
    while True:
        kind, payload = worker.events.get()
        if kind == EV_LOG:
            msg = {"type": "log", "text": payload}
        elif kind == EV_LOGIN:
            msg = {"type": "login", "logged_in": bool(payload)}
        elif kind == EV_VACANCY:
            msg = {"type": "vacancy", "vacancy": payload.to_dict()}
        elif kind == EV_RESPONSES:
            msg = {"type": "responses",
                   "items": payload["items"], "unread": payload["unread"]}
        elif kind == EV_CHAT:
            msg = {"type": "chat",
                   "vacancy_id": payload["vacancy_id"], "messages": payload["messages"]}
        else:
            continue
        _sse_queue.put(msg)


threading.Thread(target=_pump_events, daemon=True).start()


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


# ---------- API (требуют входа) ----------
@app.get("/api/config")
def api_config(user: User = Depends(current_user)):
    """Текущие критерии пользователя для заполнения формы."""
    crit = config_mod.load_for(user.id, "hh")
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
    }


@app.post("/api/save")
async def api_save(request: Request, user: User = Depends(current_user)):
    crit = config_mod.from_form(await request.json())
    config_mod.save_for(user.id, crit, "hh")
    return {"ok": True}


@app.post("/api/login")
def api_login(user: User = Depends(current_user)):
    worker.submit("login")
    return {"ok": True}


@app.post("/api/search")
async def api_search(request: Request, user: User = Depends(current_user)):
    crit = config_mod.from_form(await request.json())
    worker.submit("search", crit=crit)
    return {"ok": True}


@app.post("/api/apply")
async def api_apply(request: Request, user: User = Depends(current_user)):
    crit = config_mod.from_form(await request.json())
    worker.submit("apply", crit=crit)
    return {"ok": True}


@app.post("/api/stop")
def api_stop(user: User = Depends(current_user)):
    worker.request_stop_apply()
    return {"ok": True}


@app.post("/api/responses")
def api_responses(user: User = Depends(current_user)):
    worker.submit("responses")
    return {"ok": True}


@app.post("/api/chat")
async def api_chat(request: Request, user: User = Depends(current_user)):
    vacancy_id = str((await request.json()).get("vacancy_id", ""))
    worker.submit("chat", vacancy_id=vacancy_id)
    return {"ok": True}


@app.get("/api/suggest")
def api_suggest(text: str = "", user: User = Depends(current_user)):
    """Подсказки профессий (проксируем hh.ru, чтобы обойти CORS в браузере)."""
    return fetch_suggestions(text)


@app.get("/api/cities")
def api_cities(q: str = "", user: User = Depends(current_user)):
    """Подсказки городов из справочника по началу слова / вхождению."""
    q = (q or "").strip().lower()
    if not q:
        return []

    def rank(c):
        return (c.lower() not in _MAJOR_CITIES, len(c), c)

    starts = sorted((c for c in CITIES if c.lower().startswith(q)), key=rank)
    contains = sorted((c for c in CITIES if q in c.lower() and not c.lower().startswith(q)),
                      key=rank)
    return (starts + contains)[:10]


@app.get("/api/events")
def api_events(user: User = Depends(current_user)):
    """Поток событий (Server-Sent Events) для обновлений в реальном времени.

    Токен приходит в query (?token=), т.к. EventSource не умеет заголовки.
    Синхронный генератор: Starlette крутит его в пуле потоков, поэтому блокирующее
    ожидание очереди не мешает остальным запросам.
    """
    def stream():
        while True:
            try:
                msg = _sse_queue.get(timeout=15)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"  # heartbeat, чтобы соединение не падало
    return StreamingResponse(stream(), media_type="text/event-stream")


def main() -> None:
    url = "http://127.0.0.1:8000"
    print(f"HH-бот: откройте {url}")
    worker.submit("check_login")  # проверить вход при старте
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
