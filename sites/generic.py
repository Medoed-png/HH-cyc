"""Общий каркас адаптера сайта поиска работы.

Большинство сайтов отличаются только URL поиска и набором селекторов карточки,
поэтому общая логика (построение URL, обход страниц, парсинг карточки, детект
входа, стандартная форма критериев) вынесена сюда. Конкретный сайт = подкласс,
задающий константы. Пока селекторы поиска (CARD/TITLE_LINK) не заданы/не сверены,
search честно возвращает [] с сообщением «нужна сверка», а отклик/ответы —
заглушки (как у первого скелета). Доводим сайты до рабочего состояния по одному
с живой сверкой селекторов (как делали для hh.ru).
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlencode

from playwright.sync_api import Page

from hh_bot.search import _clean, _find_salary_text, _parse_salary_from
from hh_bot.config import Criteria
from hh_bot.models import Vacancy
from hh_bot.storage import Storage

from .base import SiteAdapter, ConfigField

Log = Callable[[str], None]

# Стандартная форма критериев (одинаковая для большинства сайтов).
_STANDARD_FIELDS = [
    ConfigField("professions", "Профессии (через запятую)", "text", "python разработчик"),
    ConfigField("region", "Город / регион", "city", "Москва"),
    ConfigField("salary_min", "Зарплата от", "number", "150000"),
    ConfigField("exclude_words", "Исключающие слова", "text"),
    ConfigField("include_words", "Обязательные слова", "text"),
    ConfigField("resume_name", "Резюме (имя)", "text"),
    ConfigField("daily_limit", "Дневной лимит откликов", "number", "100"),
    ConfigField("max_pages", "Страниц выдачи", "number", "3"),
    ConfigField("cover_letter", "Сопроводительное письмо", "textarea"),
]


class GenericSiteAdapter(SiteAdapter):
    """База для сайтов: поиск/парсинг по константам подкласса; отклик — заглушка."""

    # --- задаются в подклассе ---
    site_id = ""
    display_name = ""
    BASE = ""
    SEARCH_URL = ""            # полный URL страницы поиска
    QUERY_PARAM = "text"       # имя параметра запроса в URL
    PAGE_PARAM = "page"        # имя параметра страницы (или "" если нет)
    PAGE_BASE = 0              # с какого числа нумеруются страницы (0 или 1)
    LOGIN_URL = ""
    LOGGED_IN_MARKER = ""      # CSS-признак, что пользователь вошёл
    CARD = ""                  # селектор карточки вакансии в выдаче
    TITLE_LINK = ""            # селектор ссылки вакансии (берётся href)
    TITLE = ""                 # селектор заголовка (текст); если пусто — берётся из TITLE_LINK
    COMPANY = ""               # селектор компании
    SALARY = ""                # селектор зарплаты (опц.; иначе regex по тексту)
    ID_RE = r"(\d+)"           # как достать id вакансии из URL

    @property
    def base_url(self) -> str:
        return self.BASE

    # --- авторизация (базовая; полноценный вход — при доводке сайта) ---
    def is_logged_in(self, page: Page) -> bool:
        if page is None or not self.LOGGED_IN_MARKER:
            return False
        try:
            page.goto(self.BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            return page.locator(self.LOGGED_IN_MARKER).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def open_manual_login(self, page: Page) -> None:
        page.goto(self.LOGIN_URL or self.BASE, wait_until="domcontentloaded")

    # --- поиск ---
    def _build_url(self, query: str, page_num: int) -> str:
        params = {self.QUERY_PARAM: query}
        if self.PAGE_PARAM:
            params[self.PAGE_PARAM] = self.PAGE_BASE + page_num
        sep = "&" if "?" in self.SEARCH_URL else "?"
        return self.SEARCH_URL + sep + urlencode(params)

    def _extract_id(self, url: str) -> str:
        m = re.search(self.ID_RE, url)
        return m.group(1) if m else url

    def _blocked(self, page: Page) -> bool:
        """Показал ли сайт страницу блокировки/капчи (анти-бот). По умолчанию нет."""
        return False

    def _parse_card(self, card, profession: str) -> Vacancy | None:
        link_el = card.query_selector(self.TITLE_LINK)
        if link_el is None:
            return None
        url = link_el.get_attribute("href") or ""
        if url.startswith("/"):
            url = self.BASE + url
        url = url.split("?")[0]
        # Текст заголовка: из отдельного селектора TITLE, иначе из самой ссылки.
        title_el = (card.query_selector(self.TITLE) if self.TITLE else None) or link_el
        company_el = card.query_selector(self.COMPANY) if self.COMPANY else None
        salary_el = card.query_selector(self.SALARY) if self.SALARY else None
        salary_text = _clean((salary_el.inner_text() if salary_el else "")
                             or _find_salary_text(card.inner_text() or ""))
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
        if not (self.CARD and self.TITLE_LINK and self.SEARCH_URL):
            log(f"[{self.display_name}] поиск ещё не настроен — нужна живая сверка "
                f"селекторов сайта.")
            return []
        found: list[Vacancy] = []
        seen: set[str] = set()  # дедуп по url — и защита от «пагинация не работает»
        for page_num in range(max_pages):
            if should_stop():
                log(f"  [{self.display_name}] поиск остановлен.")
                break
            log(f"  [{self.display_name}] страница {page_num + 1}: {query}")
            page.goto(self._build_url(query, page_num), wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            if self._blocked(page):
                log(getattr(self, "BLOCK_MSG",
                            f"[{self.display_name}] доступ ограничен (анти-бот)."))
                break
            cards = page.query_selector_all(self.CARD)
            parsed = [v for v in (self._parse_card(c, query) for c in cards) if v]
            fresh = [v for v in parsed if v.url not in seen]
            if not fresh:
                if page_num == 0 and not parsed:
                    log(f"[{self.display_name}] карточки не распознаны — проверьте "
                        f"селекторы.")
                break  # нет новых вакансий (конец выдачи или пагинация без эффекта)
            for v in fresh:
                seen.add(v.url)
            found.extend(fresh)
        return found

    # --- отклик / ответы (заглушки до доводки сайта) ---
    def run_applications(self, page: Page, vacancies: list[Vacancy], crit: Criteria,
                         storage: Storage, log: Log = lambda m: None,
                         should_stop: Callable[[], bool] = lambda: False,
                         on_update: Callable[[Vacancy], None] = lambda v: None) -> int:
        log(f"Авто-отклик для {self.display_name} ещё не реализован (нужна живая сверка).")
        return 0

    def fetch_responses(self, page: Page, log: Log = lambda m: None) -> dict:
        log(f"Сбор ответов для {self.display_name} ещё не реализован.")
        return {"items": [], "unread": 0}

    def fetch_chat(self, page: Page, vacancy_id: str, log: Log = lambda m: None) -> list:
        return []

    # --- таксономия / автоподсказки ---
    def map_region(self, city_name: str) -> str:
        return (city_name or "").strip()

    def suggest_professions(self, text: str) -> list:
        return []

    def suggest_cities(self, query: str) -> list:
        return []

    def config_schema(self) -> list[dict]:
        from dataclasses import asdict
        return [asdict(f) for f in _STANDARD_FIELDS]
