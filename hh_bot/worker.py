"""Рабочий поток: владеет браузером (Playwright sync) и выполняет команды GUI.

Playwright sync-API нельзя дёргать из разных потоков, поэтому весь браузер живёт
в одном выделенном потоке. GUI общается с ним через очереди команд и событий.
"""
from __future__ import annotations

import queue
import threading

from .browser import Browser
from .config import Criteria
from . import filters
from .storage import Storage
from sites import get_adapter, DEFAULT_SITE

# Типы событий, отправляемых в GUI.
EV_LOG = "log"              # payload: str
EV_LOGIN = "login"          # payload: bool (залогинен ли)
EV_VACANCY = "vacancy"      # payload: Vacancy (новая/обновлённая)
EV_RESPONSES = "responses"  # payload: dict {items, unread}
EV_CHAT = "chat"            # payload: dict {vacancy_id, messages}
EV_DONE = "done"            # payload: str (что завершилось)


class Worker(threading.Thread):
    """Поток-исполнитель команд браузера."""

    def __init__(self, site_id: str = DEFAULT_SITE):
        super().__init__(daemon=True)
        self.commands: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self._stop_apply = threading.Event()
        self._browser: Browser | None = None
        self._storage = Storage()
        self._running = True
        self._last_suitable: list | None = None  # найденные вакансии для откликов
        # Адаптер сайта: вся специфика hh.ru/других сайтов — за ним.
        self.adapter = get_adapter(site_id)

    # --- API для GUI (потокобезопасно через очередь) ---
    def submit(self, name: str, **kwargs) -> None:
        self.commands.put((name, kwargs))

    def request_stop_apply(self) -> None:
        self._stop_apply.set()

    def shutdown(self) -> None:
        self.commands.put(("quit", {}))

    # --- внутреннее ---
    def _log(self, msg: str) -> None:
        print("[bot]", msg, flush=True)  # дублируем в консоль сервера для отладки
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
        # В таблицу выводим только подходящие вакансии.
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
        # Заносим все текущие отклики с hh.ru в память, чтобы бот не открывал
        # их повторно при поиске.
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
        # Откликаемся на уже найденные вакансии (после «Найти»); если их нет —
        # ищем заново.
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
