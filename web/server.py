"""Локальный веб-интерфейс HH-бота (Flask) поверх готового Python-бэкенда.

UI открывается в браузере на http://127.0.0.1:8000. Логин на hh.ru и парсинг —
как и раньше, через Playwright (рабочий поток Worker). Здесь — только веб-слой:
HTTP-эндпоинты + поток событий (SSE) для обновлений в реальном времени.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import webbrowser

from flask import Flask, Response, jsonify, request, send_from_directory

from hh_bot import config as config_mod
from hh_bot.cities_list import CITIES
from hh_bot.suggest import fetch_suggestions
from hh_bot.worker import Worker, EV_LOG, EV_LOGIN, EV_VACANCY, EV_RESPONSES

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Крупные города — всегда выше в подсказках, даже если название длинное.
_MAJOR_CITIES = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "краснодар", "воронеж", "пермь", "волгоград", "россия",
}

app = Flask(__name__, static_folder=None)

# Один рабочий поток на всё приложение (владеет браузером Playwright).
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
            msg = {"type": "responses", "items": payload}
        else:
            continue
        _sse_queue.put(msg)


threading.Thread(target=_pump_events, daemon=True).start()


# ---------- статика ----------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ---------- API ----------
@app.route("/api/config")
def api_config():
    """Текущие критерии для заполнения формы."""
    crit = config_mod.load()
    region_name = {v: k for k, v in CITIES.items()}.get(str(crit.region), "Россия")
    return jsonify({
        "professions": ", ".join(crit.profession_texts),
        "region": region_name,
        "salary_min": crit.salary_min,
        "exclude_words": ", ".join(crit.exclude_words),
        "include_words": ", ".join(crit.include_words),
        "resume_name": crit.resume_name,
        "cover_letter": crit.cover_letter,
        "daily_limit": crit.daily_limit,
        "max_pages": crit.max_pages,
    })


@app.route("/api/save", methods=["POST"])
def api_save():
    crit = config_mod.from_form(request.get_json(force=True))
    config_mod.save(crit)
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    worker.submit("login")
    return jsonify({"ok": True})


@app.route("/api/search", methods=["POST"])
def api_search():
    crit = config_mod.from_form(request.get_json(force=True))
    worker.submit("search", crit=crit)
    return jsonify({"ok": True})


@app.route("/api/apply", methods=["POST"])
def api_apply():
    crit = config_mod.from_form(request.get_json(force=True))
    worker.submit("apply", crit=crit)
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    worker.request_stop_apply()
    return jsonify({"ok": True})


@app.route("/api/responses", methods=["POST"])
def api_responses():
    worker.submit("responses")
    return jsonify({"ok": True})


@app.route("/api/applied")
def api_applied():
    """История откликов из локальной БД (раздел «Мои отклики»)."""
    from hh_bot.storage import Storage

    store = Storage()
    items = store.list_applied()
    store.close()
    for it in items:
        it["url"] = (f"https://hh.ru/vacancy/{it['id']}"
                     if str(it["id"]).isdigit() else "https://hh.ru/applicant/negotiations")
    return jsonify(items)


@app.route("/api/suggest")
def api_suggest():
    """Подсказки профессий (проксируем hh.ru, чтобы обойти CORS в браузере)."""
    return jsonify(fetch_suggestions(request.args.get("text", "")))


@app.route("/api/cities")
def api_cities():
    """Подсказки городов из справочника по началу слова / вхождению."""
    q = (request.args.get("q", "") or "").strip().lower()
    if not q:
        return jsonify([])
    # Сперва крупные города, затем короткие названия, затем по алфавиту.
    def rank(c):
        return (c.lower() not in _MAJOR_CITIES, len(c), c)

    starts = sorted((c for c in CITIES if c.lower().startswith(q)), key=rank)
    contains = sorted((c for c in CITIES if q in c.lower() and not c.lower().startswith(q)),
                      key=rank)
    return jsonify((starts + contains)[:10])


@app.route("/api/events")
def api_events():
    """Поток событий (Server-Sent Events) для обновлений в реальном времени."""
    def stream():
        while True:
            try:
                msg = _sse_queue.get(timeout=15)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"  # heartbeat, чтобы соединение не падало
    return Response(stream(), mimetype="text/event-stream")


def main() -> None:
    url = "http://127.0.0.1:8000"
    print(f"HH-бот: откройте {url}")
    worker.submit("check_login")  # проверить вход при старте
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # threaded=True — чтобы SSE не блокировал остальные запросы.
    app.run(host="127.0.0.1", port=8000, threaded=True, debug=False)


if __name__ == "__main__":
    main()
