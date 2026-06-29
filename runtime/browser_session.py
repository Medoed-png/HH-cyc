"""Сессия браузера одного пользователя на одном сайте.

Обобщение прежнего Worker: владеет своим Playwright-браузером (persistent-профиль
profiles/{user_id}/{site_id}/), своим хранилищем истории (Storage с тем же
скоупом) и адаптером сайта. Playwright sync-API нельзя дёргать из разных потоков,
поэтому каждая сессия живёт в своём выделенном потоке; общение — через очереди
команд и событий. Пулом сессий управляет SessionManager.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from hh_bot.browser import Browser
from hh_bot.config import Criteria
from hh_bot import filters
from hh_bot.storage import Storage
from sites import get_adapter

# Типы событий, отправляемых в UI (через SessionManager -> per-user SSE).
EV_LOG = "log"              # payload: str
EV_LOGIN = "login"          # payload: bool (залогинен ли)
EV_VACANCY = "vacancy"      # payload: Vacancy (новая/обновлённая)
EV_RESPONSES = "responses"  # payload: dict {items, unread}
EV_CHAT = "chat"            # payload: dict {vacancy_id, messages}
EV_DONE = "done"            # payload: str (что завершилось)

# Корень для пользовательских профилей браузера.
_PROFILES_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")


class BrowserSession(threading.Thread):
    """Поток-исполнитель команд браузера для пары (user_id, site_id)."""

    def __init__(self, user_id: int, site_id: str = "hh"):
        super().__init__(daemon=True)
        self.user_id = user_id
        self.site_id = site_id
        self.commands: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self._stop_apply = threading.Event()
        self._browser: Browser | None = None
        self._running = True
        self._last_suitable: list | None = None  # найденные вакансии для откликов
        self.adapter = get_adapter(site_id)
        self._storage = Storage(user_id=user_id, site_id=self.adapter.site_id)
        self._profile_dir = os.path.join(_PROFILES_ROOT, str(user_id), self.adapter.site_id)
        self.last_activity = time.monotonic()

    # --- API для сервера (потокобезопасно через очередь) ---
    def submit(self, name: str, **kwargs) -> None:
        self.touch()
        self.commands.put((name, kwargs))

    def request_stop_apply(self) -> None:
        self._stop_apply.set()

    def shutdown(self) -> None:
        self.commands.put(("quit", {}))

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    # --- внутреннее ---
    def _log(self, msg: str) -> None:
        print(f"[bot u{self.user_id}/{self.site_id}]", msg, flush=True)
        self.events.put((EV_LOG, msg))

    def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._browser = Browser(headless=False, user_data_dir=self._profile_dir)
            self._browser.start()
        return self._browser

    def _persist_cookies(self) -> None:
        """Сохранить cookies сразу (после подтверждённого входа), не дожидаясь close."""
        if self._browser is not None:
            self._browser._save_cookies()

    def run(self) -> None:
        while self._running:
            try:
                name, kwargs = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                handler = getattr(self, f"_cmd_{name}", None)
                if handler:
                    handler(**kwargs)
            except Exception as e:  # noqa: BLE001
                self._log(f"Ошибка: {e}")
                self.events.put((EV_DONE, name))
        if self._browser:
            self._browser.close()
        self._storage.close()

    # --- команды ---
    def _cmd_quit(self) -> None:
        self._running = False

    def _cmd_login(self) -> None:
        br = self._ensure_browser()
        if self.adapter.is_logged_in(br.page):
            self._log(f"Вы уже авторизованы на {self.adapter.display_name}.")
            self.events.put((EV_LOGIN, True))
        else:
            self._log("Открываю страницу входа. Войдите вручную в окне браузера.")
            self.adapter.open_manual_login(br.page)
            self.events.put((EV_LOGIN, False))
        self.events.put((EV_DONE, "login"))

    def _cmd_check_login(self) -> None:
        br = self._ensure_browser()
        logged = self.adapter.is_logged_in(br.page)
        if logged:
            self._persist_cookies()  # закрепить вход на диске сразу
        self._log("Авторизация подтверждена." if logged else "Вы ещё не вошли.")
        self.events.put((EV_LOGIN, logged))
        self.events.put((EV_DONE, "check_login"))

    def _do_search(self, crit: Criteria) -> list:
        """Найти и отфильтровать вакансии, вывести подходящие в таблицу."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            return []
        all_found = []
        for text in crit.profession_texts:
            self._log(f"Поиск: {text}")
            found = self.adapter.search(
                br.page, text, crit.region, crit.max_pages, log=self._log
            )
            all_found.extend(found)
        self._log(f"Всего найдено: {len(all_found)}")
        suitable = filters.filter_all(all_found, crit, self._storage)
        self._log(f"Подходящих по критериям: {len(suitable)}")
        for v in suitable:
            self.events.put((EV_VACANCY, v))
        self._last_suitable = suitable  # запоминаем для откликов
        return suitable

    def _cmd_search(self, crit: Criteria) -> None:
        self._do_search(crit)
        self.events.put((EV_DONE, "search"))

    def _cmd_responses(self) -> None:
        """Собрать ответы работодателей и отправить их в интерфейс."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            self.events.put((EV_DONE, "responses"))
            return
        self._log("Загружаю ответы на отклики…")
        result = self.adapter.fetch_responses(br.page, log=self._log)
        # Заносим все текущие отклики с сайта в память, чтобы не открывать повторно.
        import re
        added = 0
        for it in result["items"]:
            m = re.search(r"/vacancy/(\d+)", it.get("url", ""))
            if m and not self._storage.is_applied(m.group(1)):
                self._storage.mark_applied(m.group(1), it["title"], it["company"],
                                           source="sync")
                added += 1
        if added:
            self._log(f"Добавлено в память откликов: {added} (повторно не откликнемся).")
        self.events.put((EV_RESPONSES, result))
        self.events.put((EV_DONE, "responses"))

    def _cmd_chat(self, vacancy_id: str) -> None:
        """Прочитать чат одной вакансии по требованию."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            self.events.put((EV_CHAT, {"vacancy_id": vacancy_id, "messages": []}))
            return
        msgs = self.adapter.fetch_chat(br.page, vacancy_id, log=self._log)
        self.events.put((EV_CHAT, {"vacancy_id": vacancy_id, "messages": msgs}))
        self.events.put((EV_DONE, "chat"))

    def _cmd_apply(self, crit: Criteria) -> None:
        self._stop_apply.clear()
        if self._last_suitable:
            suitable = self._last_suitable
            self._log(f"Откликаюсь на найденные вакансии: {len(suitable)} шт.")
        else:
            self._log("Сначала ищу вакансии…")
            suitable = self._do_search(crit)

        if not suitable:
            self._log("Нет подходящих вакансий для отклика. Нажмите «Найти».")
            self.events.put((EV_DONE, "apply"))
            return

        letter = crit.cover_letter.strip()
        self._log(f"Сопроводительное письмо: "
                  f"{('«' + letter[:40] + '…»') if letter else 'ПУСТО (не задано)'}")

        count = self.adapter.run_applications(
            self._ensure_browser().page,
            suitable,
            crit,
            self._storage,
            log=self._log,
            should_stop=self._stop_apply.is_set,
            on_update=lambda v: self.events.put((EV_VACANCY, v)),
        )
        self._log(f"Готово. Отправлено откликов: {count}")
        self._last_suitable = None  # список использован
        self.events.put((EV_DONE, "apply"))
