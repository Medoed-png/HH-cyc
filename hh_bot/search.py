"""Поиск вакансий: построение URL и парсинг карточек из выдачи hh.ru."""
from __future__ import annotations

import re
from urllib.parse import urlencode

from playwright.sync_api import Page

from . import selectors
from .models import Vacancy


def build_search_url(text: str, region: int, page: int = 0) -> str:
    """Построить URL поиска hh.ru для одного запроса и номера страницы."""
    params = {
        "text": text,
        "area": region,
        "page": page,
        "items_on_page": 50,
        "order_by": "publication_time",
    }
    return selectors.SEARCH_URL + "?" + urlencode(params)


def _extract_vacancy_id(url: str) -> str:
    """Достать числовой id вакансии из ссылки вида /vacancy/12345678."""
    m = re.search(r"/vacancy/(\d+)", url)
    return m.group(1) if m else url


# Зарплата в тексте карточки: "от 100 000 ₽", "250 000 – 320 000 ₽", "до 150 000 ₽".
# Учитываем обычные и неразрывные пробелы ( ,  ).
_SALARY_RE = re.compile(
    r"(?:от\s*|до\s*)?\d[\d   ]*"
    r"(?:\s*[–\-—]\s*\d[\d   ]*)?\s*(?:₽|руб|\$|€|USD|EUR|KZT|BYN|тенге|сум)",
    re.IGNORECASE,
)


def _find_salary_text(card_text: str) -> str:
    """Найти строку с зарплатой в тексте карточки."""
    m = _SALARY_RE.search(card_text)
    return m.group(0).strip() if m else ""


def _parse_salary_from(text: str) -> int:
    """Вытащить нижнюю границу зарплаты из текста ('от 100 000 ₽' -> 100000)."""
    if not text:
        return 0
    # Берём первую группу цифр (нижняя граница диапазона / значение после «от»).
    m = re.search(r"\d[\d   ]*", text)
    if not m:
        return 0
    first = re.sub(r"[^\d]", "", m.group(0))
    return int(first) if first else 0


def _card_salary(card) -> str:
    """Текст зарплаты карточки: сперва селектор, иначе регулярка по тексту."""
    salary_el = card.query_selector(selectors.CARD_SALARY)
    if salary_el:
        return (salary_el.inner_text() or "").strip()
    return _find_salary_text(card.inner_text() or "")


def _parse_card(card, profession: str) -> Vacancy | None:
    """Разобрать одну карточку вакансии. None — если нет заголовка/ссылки."""
    title_el = card.query_selector(selectors.CARD_TITLE_LINK)
    if title_el is None:
        return None
    url = (title_el.get_attribute("href") or "")
    if url.startswith("/"):
        url = selectors.BASE + url
    url = url.split("?")[0]  # чистый id без query-параметров

    company_el = card.query_selector(selectors.CARD_COMPANY)
    salary_text = _card_salary(card)
    return Vacancy(
        vacancy_id=_extract_vacancy_id(url),
        title=(title_el.inner_text() or "").strip(),
        company=(company_el.inner_text() or "").strip() if company_el else "",
        url=url,
        salary=salary_text,
        salary_from=_parse_salary_from(salary_text),
        profession=profession,
    )


def parse_cards(page: Page, profession: str) -> list[Vacancy]:
    """Распарсить все карточки вакансий на текущей странице выдачи."""
    vacancies: list[Vacancy] = []
    for card in page.query_selector_all(selectors.VACANCY_CARD):
        vacancy = _parse_card(card, profession)
        if vacancy is not None:
            vacancies.append(vacancy)
    return vacancies


def search(page: Page, text: str, region: int, max_pages: int = 5,
           log=lambda m: None) -> list[Vacancy]:
    """Обойти страницы выдачи по одному запросу и вернуть все вакансии."""
    found: list[Vacancy] = []
    for page_num in range(max_pages):
        url = build_search_url(text, region, page_num)
        log(f"  Загружаю страницу {page_num + 1}: {text}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)  # дать догрузиться JS-выдаче
        cards = parse_cards(page, text)
        if not cards:
            log("  Больше вакансий нет.")
            break
        found.extend(cards)
    return found
