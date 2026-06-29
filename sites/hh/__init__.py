"""Адаптер сайта hh.ru.

На этапе M1 адаптер делегирует в существующие модули `hh_bot` (search/applier/
responses/suggest/selectors), консолидируя hh-специфику за единым интерфейсом
SiteAdapter. Физический перенос этих модулей внутрь sites/hh/ — последующая
косметическая правка; сейчас важнее сам шов: Worker общается только с адаптером.
"""
from __future__ import annotations

from typing import Callable

from playwright.sync_api import Page

from hh_bot import selectors
from hh_bot import search as _search
from hh_bot import applier as _applier
from hh_bot import responses as _responses
from hh_bot.suggest import fetch_suggestions
from hh_bot.cities_list import CITIES
from hh_bot.config import Criteria
from hh_bot.models import Vacancy
from hh_bot.storage import Storage

from ..base import SiteAdapter, LoginResult, LoginStatus

Log = Callable[[str], None]

# Крупные города — выше в подсказках (перенесено из web/server.py).
_MAJOR_CITIES = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "краснодар", "воронеж", "пермь", "волгоград", "россия",
}


class HHAdapter(SiteAdapter):
    site_id = "hh"
    display_name = "hh.ru"

    @property
    def base_url(self) -> str:
        return selectors.BASE

    # --- авторизация ---
    def is_logged_in(self, page: Page) -> bool:
        """Открыть страницу, требующую входа: гостя редиректит на /account/login."""
        if page is None:
            return False
        page.goto(selectors.BASE + "/applicant/resumes", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        url = page.url
        return "/account/login" not in url and "/auth/" not in url

    def open_manual_login(self, page: Page) -> None:
        page.goto(selectors.BASE + "/account/login", wait_until="domcontentloaded")

    # --- поиск ---
    def search(self, page: Page, query: str, region: str, max_pages: int,
               log: Log = lambda m: None) -> list[Vacancy]:
        found = _search.search(page, query, region, max_pages, log=log)
        for v in found:
            v.site = self.site_id
        return found

    # --- отклик ---
    def run_applications(self, page: Page, vacancies: list[Vacancy], crit: Criteria,
                         storage: Storage, log: Log = lambda m: None,
                         should_stop: Callable[[], bool] = lambda: False,
                         on_update: Callable[[Vacancy], None] = lambda v: None) -> int:
        return _applier.run_applications(page, vacancies, crit, storage, log=log,
                                         should_stop=should_stop, on_update=on_update)

    # --- ответы / чат ---
    def fetch_responses(self, page: Page, log: Log = lambda m: None) -> dict:
        return _responses.fetch_responses(page, log=log)

    def fetch_chat(self, page: Page, vacancy_id: str,
                   log: Log = lambda m: None) -> list:
        return _responses.fetch_chat(page, vacancy_id, log=log)

    # --- таксономия / автоподсказки ---
    def map_region(self, city_name: str) -> str:
        """Название города -> id области hh.ru ("113" = вся Россия по умолчанию)."""
        name = (city_name or "").strip()
        city_id = CITIES.get(name)
        if city_id is None:
            low = {k.lower(): v for k, v in CITIES.items()}
            city_id = low.get(name.lower(), "113")
        return str(city_id)

    def suggest_professions(self, text: str) -> list:
        return fetch_suggestions(text)

    def suggest_cities(self, query: str) -> list:
        q = (query or "").strip().lower()
        if not q:
            return []

        def rank(c):
            return (c.lower() not in _MAJOR_CITIES, len(c), c)

        starts = sorted((c for c in CITIES if c.lower().startswith(q)), key=rank)
        contains = sorted(
            (c for c in CITIES if q in c.lower() and not c.lower().startswith(q)),
            key=rank,
        )
        return (starts + contains)[:10]

    # --- схема формы критериев ---
    def config_schema(self) -> list[dict]:
        from ..base import ConfigField
        from dataclasses import asdict
        fields = [
            ConfigField("professions", "Профессии (через запятую)", "text",
                        "python разработчик, backend"),
            ConfigField("region", "Город / регион", "city", "Москва"),
            ConfigField("salary_min", "Зарплата от", "number", "150000"),
            ConfigField("exclude_words", "Исключающие слова", "text", "1с, php"),
            ConfigField("include_words", "Обязательные слова", "text"),
            ConfigField("resume_name", "Резюме (имя)", "text"),
            ConfigField("daily_limit", "Дневной лимит откликов", "number", "100"),
            ConfigField("max_pages", "Страниц выдачи", "number", "3"),
            ConfigField("cover_letter", "Сопроводительное письмо", "textarea"),
        ]
        return [asdict(f) for f in fields]
