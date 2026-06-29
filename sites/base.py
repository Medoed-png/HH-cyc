"""Интерфейс адаптера сайта поиска работы.

`SiteAdapter` — единая точка, за которой прячется специфика конкретного сайта
(URL-ы, селекторы, поток логина, разбор выдачи, отклик, чтение ответов/чата,
таксономия региона и автоподсказки). hh.ru — первый адаптер (sites/hh).
Добавить новый сайт = реализовать этот интерфейс и зарегистрировать в
sites/__init__.py (реестр SITES). Ядро (Worker/web) общается ТОЛЬКО с адаптером.

Объекты Vacancy/Criteria — общие, site-agnostic (hh_bot/models.py, hh_bot/config.py).
Методы принимают уже открытую вкладку Playwright `page` — жизненным циклом
браузера управляет вызывающий (Worker / будущий SessionManager).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # только для аннотаций, без рантайм-зависимости
    from playwright.sync_api import Page
    from hh_bot.config import Criteria
    from hh_bot.models import Vacancy
    from hh_bot.storage import Storage

Log = Callable[[str], None]


class LoginStatus(Enum):
    """Итог попытки серверного логина в аккаунт сайта (используется с M5)."""

    OK = "ok"
    SMS_REQUIRED = "sms_required"
    CAPTCHA_REQUIRED = "captcha_required"
    BAD_CREDENTIALS = "bad_credentials"
    FAILED = "failed"


@dataclass
class LoginResult:
    status: LoginStatus
    detail: str = ""
    challenge_token: str = ""  # непрозрачный хэндл для продолжения SMS/капчи


@dataclass
class ConfigField:
    """Описание одного поля формы критериев (для рендеринга UI по сайту)."""

    name: str
    label: str
    type: str = "text"          # text | number | textarea | city
    placeholder: str = ""
    help: str = ""


class SiteAdapter(ABC):
    """Базовый класс адаптера сайта. Все методы работают с переданной `page`."""

    site_id: str = ""           # машинный id, напр. "hh"
    display_name: str = ""      # человекочитаемое имя, напр. "hh.ru"

    # --- идентификация / URL ---
    @property
    @abstractmethod
    def base_url(self) -> str: ...

    # --- авторизация ---
    @abstractmethod
    def is_logged_in(self, page: "Page") -> bool:
        """Залогинен ли пользователь на сайте в текущей сессии браузера."""

    @abstractmethod
    def open_manual_login(self, page: "Page") -> None:
        """Открыть страницу входа для ручной авторизации (фолбэк)."""

    # Серверный логин по логину/паролю + SMS — реализуется в M5.
    def login_with_credentials(self, page: "Page", username: str, password: str,
                               log: Log = lambda m: None) -> LoginResult:
        raise NotImplementedError("серверный логин будет добавлен в M5")

    def submit_sms_code(self, page: "Page", code: str,
                        log: Log = lambda m: None) -> LoginResult:
        raise NotImplementedError("ввод SMS-кода будет добавлен в M5")

    # --- поиск ---
    @abstractmethod
    def search(self, page: "Page", query: str, region: str, max_pages: int,
               log: Log = lambda m: None) -> list["Vacancy"]:
        """Найти вакансии по одному запросу. site у вакансий проставляет адаптер."""

    # --- отклик ---
    @abstractmethod
    def run_applications(self, page: "Page", vacancies: list["Vacancy"],
                         crit: "Criteria", storage: "Storage",
                         log: Log = lambda m: None,
                         should_stop: Callable[[], bool] = lambda: False,
                         on_update: Callable[["Vacancy"], None] = lambda v: None) -> int:
        """Цикл откликов (дневной лимит, паузы, стоп). Возвращает число откликов."""

    # --- ответы работодателей / чат ---
    @abstractmethod
    def fetch_responses(self, page: "Page", log: Log = lambda m: None) -> dict:
        """Список откликов: {"items": [...], "unread": N}."""

    @abstractmethod
    def fetch_chat(self, page: "Page", vacancy_id: str,
                   log: Log = lambda m: None) -> list:
        """Сообщения чата конкретной вакансии."""

    # --- таксономия / регион / автоподсказки ---
    @abstractmethod
    def map_region(self, city_name: str) -> str:
        """Название города -> непрозрачный код региона сайта."""

    @abstractmethod
    def suggest_professions(self, text: str) -> list:
        """Автоподсказки профессий."""

    @abstractmethod
    def suggest_cities(self, query: str) -> list:
        """Автоподсказки городов."""

    # --- схема формы критериев (драйвит UI) ---
    @abstractmethod
    def config_schema(self) -> list[dict]:
        """Описание полей формы критериев для этого сайта."""
