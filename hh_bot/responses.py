"""Сбор ответов работодателей со страницы «Отклики и приглашения» hh.ru.

Статус берём из тега карточки. Для откликов, где работодатель ответил,
дополнительно открываем чат (iframe chatik.hh.ru) и читаем сами сообщения.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page

from . import selectors

# Статус из тега карточки: (селектор, метка, ответил_ли_работодатель).
_STATUS_TAGS = [
    (selectors.NEG_TAG_INTERVIEW,  "Собеседование", True),
    (selectors.NEG_TAG_DISCARD,    "Отказ",         True),
    (selectors.NEG_TAG_VIEWED,     "Просмотрен",    False),
    (selectors.NEG_TAG_NOT_VIEWED, "Не просмотрен", False),
]

# JS для извлечения сообщений из iframe чата.
_CHAT_JS = """els=>els.map(e=>({
    author: (e.querySelector('[data-qa="chat-bubble-author-name"]')||{}).textContent||'',
    text:   (e.querySelector('[data-qa="chat-bubble-text"]')||{}).textContent||'',
    time:   (e.querySelector('[data-qa="chat-buble-display-time"]')||{}).textContent||''
}))"""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _abs_url(url: str) -> str:
    return selectors.BASE + url if url.startswith("/") else url


def _status_of(card) -> tuple[str, bool]:
    for sel, label, responded in _STATUS_TAGS:
        if card.query_selector(sel):
            return label, responded
    return "Ожидание", False


def _unread_count(page: Page) -> int:
    badge = page.query_selector(selectors.NEG_UNREAD_BADGE)
    if badge:
        digits = re.sub(r"\D", "", badge.inner_text() or "")
        if digits:
            return int(digits)
    m = re.match(r"\s*(\d+)", page.title() or "")
    return int(m.group(1)) if m else 0


def _read_chat(page: Page, card, prev_url: str) -> tuple[list, str]:
    """Открыть чат карточки и прочитать сообщения (author, time, text)."""
    btn = card.query_selector(selectors.NEG_CHAT_BUTTON)
    if btn is None:
        return [], prev_url
    try:
        btn.click()
    except Exception:
        return [], prev_url

    # Ждём, пока iframe чата сменится на новый.
    frame = None
    for _ in range(20):
        page.wait_for_timeout(300)
        frame = next((f for f in page.frames if "chatik.hh.ru/chat" in (f.url or "")), None)
        if frame and frame.url != prev_url:
            break
    if frame is None:
        return [], prev_url
    page.wait_for_timeout(1200)  # дать сообщениям отрисоваться

    try:
        raw = frame.eval_on_selector_all('[data-qa="chat-bubble-wrapper"]', _CHAT_JS)
    except Exception:
        raw = []
    messages = [
        {"author": _clean(m["author"]), "time": _clean(m["time"]), "text": _clean(m["text"])}
        for m in raw if _clean(m["text"])
    ]
    return messages[-12:], frame.url  # последние сообщения


def _vacancy_id(url: str) -> str:
    m = re.search(r"/vacancy/(\d+)", url or "")
    return m.group(1) if m else ""


def _parse_item(card) -> dict | None:
    link = card.query_selector(selectors.NEG_ITEM_VACANCY)
    if link is None:
        return None
    title = _clean(link.inner_text())
    if not title:
        return None
    url = _abs_url((link.get_attribute("href") or "").split("?")[0])
    company_el = card.query_selector(selectors.NEG_ITEM_COMPANY)
    date_el = card.query_selector(selectors.NEG_ITEM_DATE)
    status, responded = _status_of(card)
    return {
        "id": _vacancy_id(url),
        "title": title,
        "company": _clean(company_el.inner_text()) if company_el else "",
        "date": _clean(date_el.inner_text()) if date_el else "",
        "status": status,
        "responded": responded,
        "url": url,
    }


def fetch_responses(page: Page, log=lambda m: None) -> dict:
    """Собрать список откликов (без чтения чатов — оно по требованию).

    Возвращает {"items": [...], "unread": N}.
    """
    page.goto(selectors.NEGOTIATIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)

    if page.query_selector(selectors.NEG_EMPTY):
        log("Откликов пока нет.")
        return {"items": [], "unread": 0}

    items = []
    for card in page.query_selector_all(selectors.NEG_ITEM):
        item = _parse_item(card)
        if item is not None:
            items.append(item)

    unread = _unread_count(page)
    answered = sum(1 for i in items if i["responded"])
    log(f"Откликов: {len(items)} · ответов работодателей: {answered} · "
        f"непрочитанных: {unread}")
    return {"items": items, "unread": unread}


def fetch_chat(page: Page, vacancy_id: str, log=lambda m: None) -> list:
    """Открыть чат конкретной вакансии и прочитать сообщения."""
    page.goto(selectors.NEGOTIATIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    for card in page.query_selector_all(selectors.NEG_ITEM):
        if card.query_selector(f'a[href*="/vacancy/{vacancy_id}"]'):
            msgs, _ = _read_chat(page, card, "")
            return msgs
    return []
