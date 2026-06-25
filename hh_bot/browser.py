"""Управление браузером через Playwright (sync API).

Используется persistent context: сессия (cookies, логин) сохраняется на диске
между запусками, поэтому логиниться руками нужно только один раз.
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright, Page, BrowserContext

from . import selectors

# Папка с пользовательскими данными браузера (сессия hh.ru).
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".browser_profile")


class Browser:
    """Обёртка над persistent-контекстом Chromium."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> Page:
        """Запустить браузер и вернуть рабочую вкладку."""
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def is_logged_in(self) -> bool:
        """Проверить, что пользователь авторизован на hh.ru.

        Надёжный способ: открыть страницу, требующую входа (/applicant/resumes).
        Гостя hh.ru редиректит на /account/login, авторизованного — оставляет.
        """
        if self.page is None:
            return False
        self.page.goto(selectors.BASE + "/applicant/resumes",
                       wait_until="domcontentloaded")
        self.page.wait_for_timeout(800)
        url = self.page.url
        return "/account/login" not in url and "/auth/" not in url

    def open_login(self) -> None:
        """Открыть страницу входа для ручной авторизации (капча/2FA проходятся руками)."""
        if self.page is None:
            self.start()
        self.page.goto(selectors.BASE + "/account/login", wait_until="domcontentloaded")

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self._pw is not None:
            self._pw.stop()
        self.context = None
        self.page = None
        self._pw = None
