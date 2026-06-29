"""Управление браузером через Playwright (sync API).

Используется persistent context: сессия (cookies, логин) сохраняется на диске
между запусками, поэтому логиниться руками нужно только один раз.

Browser — site-agnostic лаунчер вкладки. Специфика входа на конкретный сайт
(детект логина, страница входа) живёт в адаптере сайта (SiteAdapter), а не здесь.
"""
from __future__ import annotations

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
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> Page:
        """Запустить браузер и вернуть рабочую вкладку."""
        os.makedirs(self.user_data_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self._pw is not None:
            self._pw.stop()
        self.context = None
        self.page = None
        self._pw = None
