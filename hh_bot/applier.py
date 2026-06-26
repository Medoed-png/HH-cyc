"""Авто-отклик на вакансии через клики в браузере."""
from __future__ import annotations

import random
import time

from playwright.sync_api import Page

from . import selectors
from .config import Criteria
from .models import Vacancy, STATUS_APPLIED, STATUS_SKIPPED, STATUS_ERROR
from .storage import Storage

# Паузы ожидания загрузки/реакции страницы, мс.
_WAIT_PAGE = 1000
_WAIT_ACTION = 1500
_WAIT_TOGGLE = 500


def _has_captcha(page: Page) -> bool:
    return page.query_selector(selectors.CAPTCHA) is not None


def _fill_cover_letter(page: Page, text: str) -> None:
    """Вставить сопроводительное письмо, если поле доступно (ошибки игнорируем)."""
    text = text.strip()
    if not text:
        return
    toggle = page.query_selector(selectors.COVER_LETTER_TOGGLE)
    if toggle:
        try:
            toggle.click()
            page.wait_for_timeout(_WAIT_TOGGLE)
        except Exception:
            pass
    letter = page.query_selector(selectors.COVER_LETTER_INPUT)
    if letter:
        try:
            letter.fill(text)
        except Exception:
            pass


def apply_to(page: Page, vacancy: Vacancy, crit: Criteria, log=lambda m: None) -> str:
    """Откликнуться на одну вакансию. Возвращает итоговый статус.

    Последовательность: открыть вакансию → проверить капчу/повторный отклик →
    нажать «Откликнуться» → пропустить тест/анкету → вставить письмо → отправить.
    """
    page.goto(vacancy.url, wait_until="domcontentloaded")
    page.wait_for_timeout(_WAIT_PAGE)

    if _has_captcha(page):
        vacancy.note = "капча — пройдите вручную и продолжите"
        return STATUS_ERROR

    if page.query_selector(selectors.ALREADY_RESPONDED):
        vacancy.note = "уже откликались на сайте"
        return STATUS_SKIPPED

    respond_btn = page.query_selector(selectors.RESPOND_BUTTON)
    if respond_btn is None:
        vacancy.note = "кнопка отклика не найдена"
        return STATUS_SKIPPED

    respond_btn.click()
    page.wait_for_timeout(_WAIT_ACTION)

    if _has_captcha(page):
        vacancy.note = "капча после клика — пройдите вручную"
        return STATUS_ERROR

    if page.query_selector(selectors.RESPONSE_QUESTIONNAIRE):
        vacancy.note = "требуется тест/анкета — пропуск"
        return STATUS_SKIPPED

    _fill_cover_letter(page, crit.cover_letter)

    submit = page.query_selector(selectors.SUBMIT_RESPONSE)
    if submit is None:
        vacancy.note = "кнопка подтверждения не найдена"
        return STATUS_ERROR
    submit.click()
    page.wait_for_timeout(_WAIT_ACTION)

    return STATUS_APPLIED


def _interruptible_sleep(seconds: float, should_stop) -> None:
    """Пауза короткими интервалами, чтобы быстро реагировать на «Стоп»."""
    slept = 0.0
    while slept < seconds and not should_stop():
        time.sleep(0.5)
        slept += 0.5


def run_applications(page: Page, vacancies: list[Vacancy], crit: Criteria,
                     storage: Storage, log=lambda m: None,
                     should_stop=lambda: False, on_update=lambda v: None) -> int:
    """Цикл откликов с дневным лимитом и паузами. Возвращает число откликов."""
    applied_count = 0
    delay_min, delay_max = (list(crit.delay_seconds) + [20, 45])[:2]

    for vacancy in vacancies:
        if should_stop():
            log("Остановлено пользователем.")
            break
        if storage.applied_today() >= crit.daily_limit:
            log(f"Достигнут дневной лимит ({crit.daily_limit}). Останавливаюсь.")
            break

        log(f"Откликаюсь: {vacancy.title} — {vacancy.company}")
        try:
            status = apply_to(page, vacancy, crit, log)
        except Exception as e:  # noqa: BLE001
            status = STATUS_ERROR
            vacancy.note = f"ошибка: {e}"

        vacancy.status = status
        if status == STATUS_APPLIED:
            storage.mark_applied(vacancy.vacancy_id, vacancy.title, vacancy.company)
            applied_count += 1
            log(f"  ✓ Отклик отправлен ({applied_count})")
        else:
            # Если на сайте уже есть отклик (в т.ч. сделанный вручную) —
            # запоминаем, чтобы в следующий раз не открывать вакансию повторно.
            if "уже откликались" in vacancy.note:
                storage.mark_applied(vacancy.vacancy_id, vacancy.title, vacancy.company)
            log(f"  – {status}: {vacancy.note}")
        on_update(vacancy)

        # Человекоподобная пауза только после успешного отклика.
        if status == STATUS_APPLIED:
            pause = random.uniform(delay_min, delay_max)
            log(f"  Пауза {pause:.0f} c…")
            _interruptible_sleep(pause, should_stop)

    return applied_count
