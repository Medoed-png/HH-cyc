"""Управление браузером через Playwright (sync API).

Используется persistent context: сессия (cookies, логин) сохраняется на диске
между запусками, поэтому логиниться руками нужно только один раз.

Persistent-профиль НЕ сохраняет сессионные cookies (без срока истечения) при
закрытии контекста, а вход на сайт может на них опираться. Поэтому при закрытии
мы дополнительно выгружаем ВСЕ cookies в файл рядом с профилем и восстанавливаем
их при следующем запуске — тогда вход переживает и авто-закрытие по простою, и
перезапуск приложения.

Browser — site-agnostic лаунчер вкладки. Специфика входа на конкретный сайт
(детект логина, страница входа) живёт в адаптере сайта (SiteAdapter), а не здесь.
"""
from __future__ import annotations

import json
import os

from playwright.sync_api import sync_playwright, Page, BrowserContext

# Папка с пользовательскими данными браузера по умолчанию (одиночный режим).
# В мультипользовательском режиме передаётся свой профиль на (user, site).
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".browser_profile")


class Browser:
    """Обёртка над persistent-контекстом Chromium."""

    def __init__(self, headless: bool = False, user_data_dir: str | None = None):
        self.headless = headless
        self.user_data_dir = user_data_dir or USER_DATA_DIR
        # Файл с cookies рядом с профилем (включая сессионные — их профиль теряет).
        self._cookies_path = self.user_data_dir.rstrip("/\\") + ".cookies.json"
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> Page:
        """Запустить браузер, восстановить cookies и вернуть рабочую вкладку."""
        os.makedirs(self.user_data_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._restore_cookies()
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def _restore_cookies(self) -> None:
        """Подгрузить сохранённые cookies (в т.ч. сессионные) в контекст."""
        if self.context is None or not os.path.exists(self._cookies_path):
            return
        try:
            with open(self._cookies_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if cookies:
                self.context.add_cookies(cookies)
        except Exception:  # noqa: BLE001 — битый файл не должен ломать запуск
            pass

    def is_alive(self) -> bool:
        """Жив ли браузер: есть вкладка и она не закрыта (юзер мог закрыть окно)."""
        try:
            return self.page is not None and not self.page.is_closed()
        except Exception:  # noqa: BLE001 — контекст/браузер уже мёртв
            return False

    def _save_cookies(self) -> None:
        """Выгрузить все cookies контекста в файл (вызывать ДО close)."""
        if self.context is None:
            return
        try:
            cookies = self.context.cookies()
            with open(self._cookies_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self.context is not None:
            self._save_cookies()  # сохранить cookies, пока контекст ещё жив
            self.context.close()
        if self._pw is not None:
            self._pw.stop()
        self.context = None
        self.page = None
        self._pw = None
