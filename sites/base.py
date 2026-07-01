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


@dataclass
class LoginMethod:
    """Один способ входа на сайт (драйвит UI подключения аккаунта).

    id      — phone | email | manual | external (или произвольный код способа).
    fields  — какие поля показывать пользователю; подмножество
              {"username", "password", "sms_code"}. Пустой список = вход без
              ввода данных (ручной/через внешний сервис, открывается окно браузера).
    """

    id: str
    label: str
    fields: list[str] = field(default_factory=list)
    hint: str = ""


# Готовые конструкторы типовых способов входа (переиспользуют адаптеры).
def login_method_phone() -> LoginMethod:
    return LoginMethod("phone", "По номеру телефона", ["username", "sms_code"],
                       "Введите телефон → придёт код в SMS → введите код.")


def login_method_email() -> LoginMethod:
    return LoginMethod("email", "По почте", ["username", "password"],
                       "Email и пароль от аккаунта сайта.")


def login_method_manual() -> LoginMethod:
    return LoginMethod("manual", "Войти вручную в окне", [],
                       "Откроется окно браузера на странице входа — войдите сами, "
                       "сессия сохранится.")


def login_method_external(service: str = "Госуслуги") -> LoginMethod:
    return LoginMethod("external", f"Войти через {service}", [],
                       f"Вход только через {service} вручную в окне браузера.")


class SiteAdapter(ABC):
    """Базовый класс адаптера сайта. Все методы работают с переданной `page`."""

    site_id: str = ""           # машинный id, напр. "hh"
    display_name: str = ""      # человекочитаемое имя, напр. "hh.ru"
    # Иконка для ряда подключения аккаунтов в UI (цветной бейдж-буква, без файлов).
    icon_label: str = ""        # 1–3 буквы для бейджа (если пусто — две буквы имени)
    icon_color: str = "#6c757d" # hex-цвет фона бейджа

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

    def login_methods(self) -> list["LoginMethod"]:
        """Способы входа на сайт (драйвит UI подключения аккаунта).

        По умолчанию — только ручной вход в окне браузера. Сайты с серверным
        логином переопределяют (напр. hh: телефон/почта). Источник истины для UI.
        """
        return [login_method_manual()]

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
               log: Log = lambda m: None, experience: str = "",
               employment: list | None = None, schedule: list | None = None,
               should_stop: Callable[[], bool] = lambda: False
               ) -> list["Vacancy"]:
        """Найти вакансии по одному запросу. site у вакансий проставляет адаптер.

        experience/employment/schedule — необязательные фильтры (коды сайта); сайт
        вправе их игнорировать, если не поддерживает. should_stop — проверять между
        страницами выдачи, чтобы кнопка «Стоп» прерывала длинное сканирование.
        """

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

    # --- описание вакансии (для генерации письма; НЕ требует входа) ---
    def fetch_description(self, page: "Page", url: str) -> str:
        """Открыть публичную страницу вакансии и вернуть текст описания.

        База — весь текст страницы (best-effort); сайты с известным селектором
        описания переопределяют для более чистого текста (см. sites/hh).
        """
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            return (page.inner_text("body") or "")[:8000]
        except Exception:  # noqa: BLE001
            return ""

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
