"""Пул per-user сессий браузера (in-process, без Redis).

Заменяет единственный глобальный Worker: на каждую активную пару (user_id,
site_id) поднимается своя BrowserSession (свой браузер/профиль/история). Сессии
создаются лениво при первой команде, закрываются по простою (idle-reaper), при
переполнении вытесняется самая давно неиспользуемая (LRU). События каждой сессии
маршрутизируются ТОЛЬКО её пользователю через колбэк publish(user_id, msg).

Redis (pub/sub событий и распределённая очередь) появится при переезде в облако
(M8); сейчас всё в одном процессе.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable

from .browser_session import (
    BrowserSession, EV_LOG, EV_LOGIN, EV_VACANCY, EV_RESPONSES, EV_CHAT, EV_CONN,
)

Publish = Callable[[int, dict], None]


def event_to_msg(kind: str, payload) -> dict | None:
    """Событие сессии (EV_*) -> JSON-сообщение для SSE (как в старом _pump_events)."""
    if kind == EV_LOG:
        return {"type": "log", "text": payload}
    if kind == EV_LOGIN:
        return {"type": "login", "logged_in": bool(payload)}
    if kind == EV_VACANCY:
        return {"type": "vacancy", "vacancy": payload.to_dict()}
    if kind == EV_RESPONSES:
        return {"type": "responses", "items": payload["items"], "unread": payload["unread"]}
    if kind == EV_CHAT:
        return {"type": "chat",
                "vacancy_id": payload["vacancy_id"], "messages": payload["messages"]}
    if kind == EV_CONN:
        return {"type": "conn_status", **payload}
    return None  # EV_DONE и прочее в UI не транслируем


class SessionManager:
    """Пул сессий по ключу (user_id, site_id)."""

    def __init__(self, publish: Publish, max_sessions: int | None = None,
                 idle_timeout: float | None = None):
        self._publish = publish
        self._sessions: dict[tuple[int, str], BrowserSession] = {}
        self._lock = threading.RLock()
        # Настраиваются через окружение (для облака); локальные дефолты щадящие,
        # чтобы браузер не закрывался слишком часто.
        self._max = max_sessions if max_sessions is not None \
            else int(os.environ.get("MAX_SESSIONS", "6"))
        self._idle_timeout = idle_timeout if idle_timeout is not None \
            else float(os.environ.get("SESSION_IDLE_SECONDS", "1800"))
        threading.Thread(target=self._reaper, daemon=True).start()

    # --- публичный API ---
    def submit(self, user_id: int, site_id: str, name: str, **kwargs) -> None:
        self.get_or_create(user_id, site_id).submit(name, **kwargs)

    def request_stop_apply(self, user_id: int, site_id: str = "hh") -> None:
        with self._lock:
            sess = self._sessions.get((user_id, site_id))
        if sess:
            sess.request_stop_apply()

    def get_or_create(self, user_id: int, site_id: str = "hh") -> BrowserSession:
        with self._lock:
            key = (user_id, site_id)
            sess = self._sessions.get(key)
            if sess is not None:
                sess.touch()
                return sess
            self._evict_if_needed()
            sess = BrowserSession(user_id, site_id)
            sess.start()
            self._sessions[key] = sess
            self._start_pump(sess)
            print(f"[pool] сессия создана u{user_id}/{site_id} "
                  f"(всего {len(self._sessions)})", flush=True)
            return sess

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def shutdown_all(self, join_timeout: float = 10.0) -> None:
        """Корректно закрыть все сессии (каждая сохранит cookies и закроет браузер).

        Вызывается при остановке приложения: иначе daemon-потоки сессий гибнут без
        close(), и сессионные cookies (вход на сайт) не сохраняются на диск.
        """
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.shutdown()  # ставит команду quit -> сессия закроет браузер сама
        for sess in sessions:
            sess.join(timeout=join_timeout)

    # --- внутреннее ---
    def _start_pump(self, session: BrowserSession) -> None:
        """Поток-насос: события сессии -> publish её пользователю."""
        def pump():
            while session._running or not session.events.empty():
                try:
                    kind, payload = session.events.get(timeout=0.5)
                except queue.Empty:
                    continue
                msg = event_to_msg(kind, payload)
                if msg is not None:
                    try:
                        self._publish(session.user_id, msg)
                    except Exception:  # noqa: BLE001 — подписчик мог отвалиться
                        pass
        threading.Thread(target=pump, daemon=True).start()

    def _evict_if_needed(self) -> None:
        """Под локом: при переполнении закрыть самую давно неиспользуемую сессию."""
        if len(self._sessions) < self._max:
            return
        key, sess = max(self._sessions.items(), key=lambda kv: kv[1].idle_seconds())
        print(f"[pool] вытесняю по лимиту u{key[0]}/{key[1]} "
              f"(простой {sess.idle_seconds():.0f}c)", flush=True)
        self._close(key, sess)

    def _close(self, key: tuple[int, str], sess: BrowserSession) -> None:
        sess.shutdown()  # браузер закроется в потоке сессии после команды quit
        self._sessions.pop(key, None)

    def _reaper(self) -> None:
        """Фоновая чистка простаивающих сессий."""
        while True:
            time.sleep(60)
            with self._lock:
                stale = [(k, s) for k, s in self._sessions.items()
                         if s.idle_seconds() > self._idle_timeout]
                for k, s in stale:
                    print(f"[pool] закрываю по простою u{k[0]}/{k[1]}", flush=True)
                    self._close(k, s)
