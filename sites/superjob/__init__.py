"""Адаптер сайта superjob.ru — второй сайт для проверки абстракции SiteAdapter.

Назначение (M7): показать, что добавление нового сайта = реализация интерфейса
SiteAdapter + регистрация в sites/__init__.py, БЕЗ изменений ядра (web/SessionManager/
filters/applier-цикла). Поиск реализован best-effort по типичной вёрстке SuperJob;
отклик/ответы — честные заглушки до живой сверки селекторов (см. selectors.py).

⚠️ Селекторы SuperJob НЕ сверены вживую. Чтобы сайт реально заработал на запись
(отклик) — нужна живая проверка на аккаунте, аналогично hh.ru.
"""
from __future__ import annotations

import re
from typing import Callable

from playwright.sync_api import Page

# Переиспользуем site-agnostic помощники парсинга зарплаты/текста из hh-поиска.
from hh_bot.search import _find_salary_text, _parse_salary_from, _clean
from hh_bot.config import Criteria
from hh_bot.models import Vacancy
from hh_bot.storage import Storage

from . import selectors
from ..base import (
    SiteAdapter, ConfigField,
    LoginMethod, login_method_email, login_method_phone, login_method_manual,
)

Log = Callable[[str], None]


class SuperJobAdapter(SiteAdapter):
    site_id = "superjob"
    display_name = "SuperJob"
    icon_label = "SJ"
    icon_color = "#19a463"

    @property
    def base_url(self) -> str:
        return selectors.BASE

    # --- способы входа (объявлены; серверный вход добавим со сверкой) ---
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_email(), login_method_phone(), login_method_manual()]

    # --- авторизация ---
    def is_logged_in(self, page: Page) -> bool:
        if page is None:
            return False
        page.goto(selectors.BASE + "/account/", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        url = page.url
        if "/auth/" in url or "/login" in url:
            return False
        try:
            return page.locator(selectors.LOGGED_IN_MARKER).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def open_manual_login(self, page: Page) -> None:
        page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")

    # --- поиск ---
    def _build_search_url(self, text: str, page_num: int) -> str:
        from urllib.parse import urlencode
        # SuperJob: страницы 1-based. Регион (geo) — best-effort опускаем до живой сверки.
        return selectors.SEARCH_URL + "?" + urlencode(
            {"keywords": text, "page": page_num + 1}
        )

    def _extract_id(self, url: str) -> str:
        """id вакансии SuperJob: число в конце ссылки /vakansii/...-<id>.html."""
        m = re.search(r"-(\d+)\.html", url) or re.search(r"/(\d+)(?:\.html)?$", url)
        return m.group(1) if m else url

    def _parse_card(self, card, profession: str) -> Vacancy | None:
        title_el = card.query_selector(selectors.CARD_TITLE_LINK)
        if title_el is None:
            return None
        url = title_el.get_attribute("href") or ""
        if url.startswith("/"):
            url = selectors.BASE + url
        url = url.split("?")[0]
        company_el = card.query_selector(selectors.CARD_COMPANY)
        salary_el = card.query_selector(selectors.CARD_SALARY)
        salary_text = _clean(
            (salary_el.inner_text() if salary_el else "") or _find_salary_text(card.inner_text() or "")
        )
        return Vacancy(
            vacancy_id=self._extract_id(url),
            title=_clean(title_el.inner_text()),
            company=_clean(company_el.inner_text()) if company_el else "",
            url=url,
            salary=salary_text,
            salary_from=_parse_salary_from(salary_text),
            profession=profession,
            site=self.site_id,
        )

    def search(self, page: Page, query: str, region: str, max_pages: int,
               log: Log = lambda m: None, experience: str = "",
               employment: list | None = None, schedule: list | None = None,
               should_stop: Callable[[], bool] = lambda: False
               ) -> list[Vacancy]:
        # Фильтры опыта/занятости/графика для SuperJob пока не поддержаны (best-effort).
        # Регион (передаётся кодом hh.ru) к SuperJob не применим — честно предупреждаем,
        # чтобы пользователь понимал, что выдача всероссийская (113 = вся Россия).
        if region and str(region) != "113":
            log("  [SuperJob] фильтр региона не применён — выдача по всей России "
                "(geo-параметр SuperJob ещё не сверён).")
        found: list[Vacancy] = []
        seen: set[str] = set()  # дедуп по url + детект «пагинация вернула ту же страницу»
        for page_num in range(max_pages):
            if should_stop():
                log("  [SuperJob] поиск остановлен.")
                break
            url = self._build_search_url(query, page_num)
            log(f"  [SuperJob] страница {page_num + 1}: {query}")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            cards = page.query_selector_all(selectors.VACANCY_CARD)
            parsed = [v for v in (self._parse_card(c, query) for c in cards) if v]
            fresh = [v for v in parsed if v.url not in seen]
            if not fresh:  # пусто или повтор предыдущей страницы — конец выдачи
                if page_num == 0 and not parsed:
                    log("  [SuperJob] карточки не распознаны — вероятно, нужны "
                        "актуальные селекторы (sites/superjob/selectors.py).")
                break
            for v in fresh:
                seen.add(v.url)
            found.extend(fresh)
        return found

    # --- отклик / ответы (заглушки до живой сверки SuperJob) ---
    def run_applications(self, page: Page, vacancies: list[Vacancy], crit: Criteria,
                         storage: Storage, log: Log = lambda m: None,
                         should_stop: Callable[[], bool] = lambda: False,
                         on_update: Callable[[Vacancy], None] = lambda v: None) -> int:
        log("Авто-отклик для SuperJob ещё не реализован (нужна живая сверка формы отклика).")
        return 0

    def fetch_responses(self, page: Page, log: Log = lambda m: None) -> dict:
        log("Сбор ответов для SuperJob ещё не реализован.")
        return {"items": [], "unread": 0}

    def fetch_chat(self, page: Page, vacancy_id: str, log: Log = lambda m: None) -> list:
        return []

    # --- таксономия / автоподсказки ---
    def map_region(self, city_name: str) -> str:
        # Карта городов SuperJob (geo) появится при живой сверке; пока пробрасываем имя.
        return (city_name or "").strip()

    def suggest_professions(self, text: str) -> list:
        return []  # живые подсказки SuperJob — позже

    def suggest_cities(self, query: str) -> list:
        return []

    # --- схема формы критериев (те же поля Criteria, что у hh) ---
    def config_schema(self) -> list[dict]:
        from dataclasses import asdict
        fields = [
            ConfigField("professions", "Профессии (через запятую)", "text",
                        "python разработчик, backend"),
            ConfigField("region", "Город / регион", "city", "Москва"),
            ConfigField("salary_min", "Зарплата от", "number", "150000"),
            ConfigField("exclude_words", "Исключающие слова", "text"),
            ConfigField("include_words", "Обязательные слова", "text"),
            ConfigField("resume_name", "Резюме (имя)", "text"),
            ConfigField("daily_limit", "Дневной лимит откликов", "number", "100"),
            ConfigField("max_pages", "Страниц выдачи", "number", "3"),
            ConfigField("cover_letter", "Сопроводительное письмо", "textarea"),
        ]
        return [asdict(f) for f in fields]
