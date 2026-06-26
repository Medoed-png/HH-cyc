"""Сбор ответов работодателей со страницы «Отклики и приглашения» hh.ru."""
from __future__ import annotations

import re

from playwright.sync_api import Page

from . import selectors

# Классификация статуса отклика по тексту карточки (ключевые слова -> метка).
_STATUS_RULES = [
    (r"приглаш",            "Приглашение"),
    (r"оффер|предложени",   "Оффер"),
    (r"отказ|не подош",     "Отказ"),
    (r"не\s*просмотр",      "Не просмотрено"),
    (r"просмотр",           "Просмотрено"),
    (r"новое сообщ|ответил","Сообщение"),
]


def _classify(text: str) -> str:
    low = text.lower()
    for pattern, label in _STATUS_RULES:
        if re.search(pattern, low):
            return label
    return "Ожидание"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _abs_url(url: str) -> str:
    if url.startswith("/"):
        return selectors.BASE + url
    return url


def _parse_item(card) -> dict | None:
    """Разобрать карточку одного отклика."""
    title_el = card.query_selector(selectors.NEG_ITEM_TITLE)
    if title_el is None:
        return None
    title = _clean(title_el.inner_text())
    if not title:
        return None
    url = _abs_url((title_el.get_attribute("href") or selectors.NEGOTIATIONS_URL).split("?")[0])

    emp_el = card.query_selector(selectors.NEG_ITEM_EMPLOYER)
    company = _clean(emp_el.inner_text()) if emp_el else ""

    card_text = _clean(card.inner_text())
    return {
        "title": title,
        "company": company,
        "status": _classify(card_text),
        "url": url,
    }


def fetch_responses(page: Page, log=lambda m: None) -> list[dict]:
    """Открыть «Отклики и приглашения» и собрать ответы работодателей."""
    page.goto(selectors.NEGOTIATIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    if page.query_selector(selectors.NEG_EMPTY):
        log("Откликов пока нет.")
        return []

    cards = page.query_selector_all(selectors.NEG_ITEM)
    if not cards:
        # Запасной разбор — ТОЛЬКО внутри контейнера откликов, чтобы не зацепить
        # рекомендованные вакансии на странице.
        container = page.query_selector(selectors.NEG_LIST)
        if container is None:
            log("Список откликов не распознан (вёрстка hh.ru). "
                "Пришлите скриншот этой страницы — подстрою селекторы.")
            return []
        seen, results = set(), []
        for link in container.query_selector_all('a[href*="/vacancy/"]'):
            title = _clean(link.inner_text())
            url = _abs_url((link.get_attribute("href") or "").split("?")[0])
            if not title or url in seen:
                continue
            seen.add(url)
            results.append({"title": title, "company": "", "status": "Ответ", "url": url})
        log(f"Ответов получено: {len(results)}")
        return results

    results = []
    for card in cards:
        item = _parse_item(card)
        if item is not None:
            results.append(item)
    log(f"Ответов получено: {len(results)}")
    return results
