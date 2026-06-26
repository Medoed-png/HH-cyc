"""Рабочий поток: владеет браузером (Playwright sync) и выполняет команды GUI.

Playwright sync-API нельзя дёргать из разных потоков, поэтому весь браузер живёт
в одном выделенном потоке. GUI общается с ним через очереди команд и событий.
"""
from __future__ import annotations

import queue
import threading

from .browser import Browser
from .config import Criteria
from . import search as search_mod
from . import filters
from . import applier
from . import responses as responses_mod
from .storage import Storage

# Типы событий, отправляемых в GUI.
EV_LOG = "log"              # payload: str
EV_LOGIN = "login"          # payload: bool (залогинен ли)
EV_VACANCY = "vacancy"      # payload: Vacancy (новая/обновлённая)
EV_RESPONSES = "responses"  # payload: list[dict] (ответы работодателей)
EV_DONE = "done"            # payload: str (что завершилось)


class Worker(threading.Thread):
    """Поток-исполнитель команд браузера."""

    def __init__(self):
        super().__init__(daemon=True)
        self.commands: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self._stop_apply = threading.Event()
        self._browser: Browser | None = None
        self._storage = Storage()
        self._running = True

    # --- API для GUI (потокобезопасно через очередь) ---
    def submit(self, name: str, **kwargs) -> None:
        self.commands.put((name, kwargs))

    def request_stop_apply(self) -> None:
        self._stop_apply.set()

    def shutdown(self) -> None:
        self.commands.put(("quit", {}))

    # --- внутреннее ---
    def _log(self, msg: str) -> None:
        self.events.put((EV_LOG, msg))

    def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._browser = Browser(headless=False)
            self._browser.start()
        return self._browser

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
        if br.is_logged_in():
            self._log("Вы уже авторизованы на hh.ru.")
            self.events.put((EV_LOGIN, True))
        else:
            self._log("Открываю страницу входа. Войдите вручную в окне браузера.")
            br.open_login()
            self.events.put((EV_LOGIN, False))
        self.events.put((EV_DONE, "login"))

    def _cmd_check_login(self) -> None:
        br = self._ensure_browser()
        logged = br.is_logged_in()
        self._log("Авторизация подтверждена." if logged else "Вы ещё не вошли.")
        self.events.put((EV_LOGIN, logged))
        self.events.put((EV_DONE, "check_login"))

    def _do_search(self, crit: Criteria) -> list:
        """Найти и отфильтровать вакансии, вывести подходящие в таблицу."""
        br = self._ensure_browser()
        if not br.is_logged_in():
            self._log("Сначала войдите на hh.ru (кнопка «Войти»).")
            return []
        all_found = []
        for text in crit.profession_texts:
            self._log(f"Поиск: {text}")
            found = search_mod.search(
                br.page, text, crit.region, crit.max_pages, log=self._log
            )
            all_found.extend(found)
        self._log(f"Всего найдено: {len(all_found)}")
        suitable = filters.filter_all(all_found, crit, self._storage)
        self._log(f"Подходящих по критериям: {len(suitable)}")
        # В таблицу выводим только подходящие вакансии.
        for v in suitable:
            self.events.put((EV_VACANCY, v))
        return suitable

    def _cmd_search(self, crit: Criteria) -> None:
        self._do_search(crit)
        self.events.put((EV_DONE, "search"))

    def _cmd_responses(self) -> None:
        """Собрать ответы работодателей и отправить их в интерфейс."""
        br = self._ensure_browser()
        if not br.is_logged_in():
            self._log("Сначала войдите на hh.ru (кнопка «Войти»).")
            self.events.put((EV_DONE, "responses"))
            return
        self._log("Загружаю ответы на отклики…")
        items = responses_mod.fetch_responses(br.page, log=self._log)
        self.events.put((EV_RESPONSES, items))
        self.events.put((EV_DONE, "responses"))

    def _cmd_apply(self, crit: Criteria) -> None:
        self._stop_apply.clear()
        suitable = self._do_search(crit)
        if not suitable:
            self._log("Нет подходящих вакансий для отклика.")
            self.events.put((EV_DONE, "apply"))
            return
        self._log(f"Начинаю отклики ({len(suitable)} вакансий)…")
        count = applier.run_applications(
            self._ensure_browser().page,
            suitable,
            crit,
            self._storage,
            log=self._log,
            should_stop=self._stop_apply.is_set,
            on_update=lambda v: self.events.put((EV_VACANCY, v)),
        )
        self._log(f"Готово. Отправлено откликов: {count}")
        self.events.put((EV_DONE, "apply"))
