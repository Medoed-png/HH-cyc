"""Авто-отклик на вакансии через клики в браузере."""
from __future__ import annotations

import random
import re
import time

from playwright.sync_api import Frame, Page

from . import antiban
from . import letter as letter_mod
from . import selectors
from .config import Criteria
from .models import Vacancy, STATUS_APPLIED, STATUS_SKIPPED, STATUS_ERROR
from .storage import Storage

# Паузы ожидания загрузки/реакции страницы, мс.
_WAIT_PAGE = 1000
_WAIT_ACTION = 1500
_WAIT_TOGGLE = 500
# Сколько максимум ждём появления inline-поля письма, прежде чем уйти в чат, c.
_INLINE_WAIT_SECONDS = 10.0
# Короткая проба: если за это время в DOM нет НИ поля письма, НИ его информера —
# это одно-кликовый отклик (кладовщик/грузчик), не ждём весь дедлайн, а сразу
# уходим в фолбэк-чат. Для медсестры поле появляется в DOM быстрее этого порога.
_INLINE_PROBE_SECONDS = 4.0


def _has_captcha(page: Page) -> bool:
    return page.query_selector(selectors.CAPTCHA) is not None


def _dismiss_popup(page: Page) -> None:
    """Закрыть попап «дополнительные данные», если он перекрыл поле/кнопку."""
    btn = page.query_selector(selectors.DATA_COLLECTOR_CLOSE)
    if btn:
        try:
            btn.click()
            page.wait_for_timeout(_WAIT_TOGGLE)
        except Exception:  # noqa: BLE001
            pass


def _norm(text: str) -> str:
    """Схлопнуть пробелы и привести к нижнему регистру для сравнения текста."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


# --- INLINE-путь (поле письма прямо на странице вакансии) ----------------------

def _find_letter_field(page: Page):
    """Первое ВИДИМОЕ поле письма из COVER_LETTER_INPUT или None.

    Первый textarea в DOM бывает скрытым (шаблон/копия), поэтому перебираем все
    совпадения и возвращаем первое реально видимое.
    """
    for el in page.query_selector_all(selectors.COVER_LETTER_INPUT):
        try:
            if el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            pass
    return None


def _letter_field_in_dom(page: Page) -> bool:
    """Есть ли в DOM хоть какой-то намёк на inline-поле письма (видимое или нет).

    Различает «поле ещё монтируется/свёрнуто» (есть informer или textarea —
    стоит подождать) и «поля нет вообще» (одно-кликовый отклик — сразу в чат).
    """
    return (page.query_selector(selectors.COVER_LETTER_INPUT) is not None
            or page.query_selector(selectors.COVER_LETTER_INFORMER) is not None)


def _reveal_letter_field(page: Page) -> None:
    """Помочь полю письма проявиться, если оно лениво/свёрнуто.

    Видимого поля нет по двум причинам:
      • информер ещё не смонтирован — подкручиваем страницу, чтобы вызвать его
        ленивую отрисовку;
      • textarea есть в DOM, но свёрнут (display:none) — кликаем по контейнеру
        информера, чтобы развернуть.
    Контейнер трогаем только когда видимого поля ещё нет, а textarea внутри уже
    присутствует — без лишних кликов и побочных эффектов.
    """
    informer = page.query_selector(selectors.COVER_LETTER_INFORMER)
    if informer is None:
        try:
            page.mouse.wheel(0, 700)  # вызвать ленивую отрисовку информера ниже
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        informer.scroll_into_view_if_needed()
        if informer.query_selector("textarea") and _find_letter_field(page) is None:
            informer.click()  # развернуть свёрнутое поле
            page.wait_for_timeout(_WAIT_TOGGLE)
    except Exception:  # noqa: BLE001
        pass


def _send_cover_letter(page: Page, text: str, log) -> bool:
    """INLINE-путь: дождаться поля письма, вписать текст и отправить.

    Поле появляется асинхронно; мешать могут попап «доп. данные», тост-плашка
    «Отклик отправлен» и лениво/свёрнутый информер. Поэтому на каждой итерации
    закрываем попап, ищем видимое поле, пробуем его проявить (скролл + разворот).
    Если за короткую пробу поля нет в DOM ВООБЩЕ — это одно-кликовый отклик,
    выходим сразу (вернёт False → вызывающий уйдёт в чат). True — только если
    письмо реально вписано и отправлено inline; при неудаче письмо НЕ
    отправляется (нет двойной отправки).
    """
    text = (text or "").strip()
    if not text:
        return False

    letter = None
    deadline = time.monotonic() + _INLINE_WAIT_SECONDS
    probe_until = time.monotonic() + _INLINE_PROBE_SECONDS
    while time.monotonic() < deadline:
        _dismiss_popup(page)
        letter = _find_letter_field(page)
        if letter is not None:
            break
        _reveal_letter_field(page)
        letter = _find_letter_field(page)
        if letter is not None:
            break
        # Ранний выход в чат: поля письма нет в DOM и проба истекла.
        if time.monotonic() > probe_until and not _letter_field_in_dom(page):
            return False
        page.wait_for_timeout(500)

    if letter is None:
        return False  # фолбэк-сообщение пишет вызывающий _deliver_cover_letter

    try:
        letter.scroll_into_view_if_needed()
        letter.click()
        letter.fill(text)
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось вписать письмо: {e}")
        return False

    _dismiss_popup(page)
    submit = page.query_selector(selectors.SUBMIT_RESPONSE)
    if submit is None:
        log("  кнопка отправки письма не найдена")
        return False
    try:
        submit.click()
        page.wait_for_timeout(_WAIT_ACTION)
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось отправить письмо: {e}")
        return False
    return True


# --- ФОЛБЭК-путь (письмо отдельным сообщением в чат работодателя) ---------------

def _letter_already_in_chat(frame: Frame, text: str) -> bool:
    """Есть ли уже сообщение с текстом письма.

    Используется и как защита от двойной отправки (перед отправкой), и как
    подтверждение доставки (после). Сравниваем нормализованный префикс письма
    (60 симв.) с текстом пузырей — бабл показывает письмо целиком.
    """
    needle = _norm(text)[:60]
    if not needle:
        return False
    try:
        bubbles = frame.eval_on_selector_all(
            selectors.CHAT_BUBBLE_TEXT, "els => els.map(e => e.textContent || '')")
    except Exception:  # noqa: BLE001
        return False
    return any(needle in _norm(b) for b in bubbles)


def _open_chat_frame(page: Page, vacancy_id: str, log) -> Frame | None:
    """Открыть чат ИМЕННО этой вакансии и вернуть его iframe (chatik.hh.ru/chat).

    На странице вакансии кнопки чата нет — она есть только в карточке отклика на
    /applicant/negotiations. Поэтому идём туда, находим карточку по id вакансии
    и жмём «Перейти в чат». Образец доступа к iframe — responses._read_chat.
    """
    if not vacancy_id:
        return None
    prev_urls = {f.url for f in page.frames
                 if selectors.CHAT_FRAME_URL_PART in (f.url or "")}

    page.goto(selectors.NEGOTIATIONS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(_WAIT_ACTION)

    card = None
    for c in page.query_selector_all(selectors.NEG_ITEM):
        if c.query_selector(f'a[href*="/vacancy/{vacancy_id}"]'):
            card = c
            break
    if card is None:
        log("  отклик не найден в списке — письмо через чат невозможно")
        return None

    btn = card.query_selector(selectors.NEG_CHAT_BUTTON)
    if btn is None:
        log("  кнопка чата не найдена в карточке отклика")
        return None
    try:
        btn.click()
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось открыть чат: {e}")
        return None

    found = None
    for _ in range(20):  # до ~6 c ждём iframe чата
        page.wait_for_timeout(300)
        frame = next((f for f in page.frames
                      if selectors.CHAT_FRAME_URL_PART in (f.url or "")), None)
        if frame is None:
            continue
        found = frame
        if frame.url not in prev_urls:  # появился/сменился на чат этой вакансии
            break
    if found is None:
        log("  чат не открылся (iframe не появился)")
        return None
    page.wait_for_timeout(_WAIT_ACTION)  # дать чату прогрузиться
    return found


def _send_letter_via_chat(page: Page, vacancy_id: str, text: str, log) -> bool:
    """Фолбэк: отправить письмо отдельным сообщением в чат работодателя.

    Inline-поле письма для части вакансий (кладовщик/грузчик) не появляется.
    Тогда открываем чат вакансии (iframe chatik.hh.ru) и пишем письмо обычным
    сообщением — для работодателя результат эквивалентен (текст виден в чате;
    inline-письмо тоже отображается там же как chat-bubble). True — если ушло.
    """
    text = (text or "").strip()
    if not text:
        return False

    frame = _open_chat_frame(page, vacancy_id, log)
    if frame is None:
        return False

    # Ждём поле ввода сообщения внутри iframe (чат грузится асинхронно).
    box = None
    for _ in range(20):  # до ~6 c
        try:
            candidate = frame.query_selector(selectors.CHAT_MESSAGE_INPUT)
            if candidate is not None and candidate.is_visible():
                box = candidate
                break
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)
    if box is None:
        log("  поле ввода чата не появилось — письмо не отправлено")
        return False

    # Защита от двойной отправки: письмо уже среди сообщений — выходим.
    if _letter_already_in_chat(frame, text):
        log("  письмо уже есть в чате — повторно не отправляю")
        return True

    try:
        antiban.human_type(page, box, text)  # посимвольный ввод письма
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось вписать письмо в чат: {e}")
        return False

    send = frame.query_selector(selectors.CHAT_SEND_BUTTON)
    if send is None:
        log("  кнопка отправки в чате не найдена")
        return False
    try:
        antiban.human_click(page, send)
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось отправить письмо в чат: {e}")
        return False

    # Подтверждение по появлению пузыря-сообщения. Клик отправки уже прошёл —
    # при несчитанном подтверждении НЕ перекликиваем (иначе дубль).
    for _ in range(10):  # до ~3 c
        page.wait_for_timeout(300)
        if _letter_already_in_chat(frame, text):
            log("  письмо отправлено сообщением в чат")
            return True
    log("  письмо отправлено в чат (подтверждение не считано)")
    return True


def _deliver_cover_letter(page: Page, vacancy_id: str, text: str, log) -> str:
    """Гибрид доставки письма. Возвращает способ: "inline" | "chat" | "".

    Сначала быстрый inline-путь (поле письма на странице вакансии). Если поля
    нет (кладовщик/грузчик) — фолбэк: открыть чат вакансии через страницу
    «Отклики» и отправить письмо сообщением. Inline-путь при неудаче письмо НЕ
    отправляет, поэтому двойной отправки нет.
    """
    if not (text or "").strip():
        return ""
    # Доставка письма СООБЩЕНИЕМ В ЧАТ — единый надёжный путь для всех типов
    # вакансий (inline-поле на части вакансий отсутствует или подменяется чужим
    # textarea). Чат существует после любого созданного отклика.
    if _send_letter_via_chat(page, vacancy_id, text, log):
        return "chat"
    return ""


def _select_resume(page: Page, resume_name: str, log) -> None:
    """Выбрать резюме по имени, если на странице есть селектор резюме.

    ⚠️ best-effort: механика выбора резюме hh.ru (нативный select или кастомный
    дропдаун) требует живой сверки. Если контрол не найден — ничего не делаем
    (hh использует резюме по умолчанию / единственное).
    """
    name = (resume_name or "").strip()
    if not name:
        return
    sel = page.query_selector(selectors.RESUME_SELECT)
    if sel is None:
        return
    try:
        tag = (sel.evaluate("e => e.tagName") or "").lower()
        if tag == "select":
            page.select_option(selectors.RESUME_SELECT, label=name)
            page.wait_for_timeout(_WAIT_TOGGLE)
            return
        # Кастомный дропдаун: открыть и выбрать пункт по тексту имени резюме.
        antiban.human_click(page, sel)
        page.wait_for_timeout(_WAIT_TOGGLE)
        option = page.query_selector(f'text="{name}"')
        if option is not None:
            antiban.human_click(page, option)
            page.wait_for_timeout(_WAIT_TOGGLE)
    except Exception as e:  # noqa: BLE001 — выбор резюме не должен ломать отклик
        log(f"  не удалось выбрать резюме «{name}»: {e}")


def apply_to(page: Page, vacancy: Vacancy, crit: Criteria, log=lambda m: None) -> str:
    """Откликнуться на одну вакансию. Возвращает итоговый статус.

    На hh.ru клик «Откликнуться» сразу создаёт отклик. Сопроводительное письмо
    доставляем гибридно: сначала inline-поле (медсестра/комплектовщик), а если
    его нет (кладовщик/грузчик) — отдельным сообщением в чат работодателя.
    """
    page.goto(vacancy.url, wait_until="domcontentloaded")
    page.wait_for_timeout(_WAIT_PAGE)

    # Текст описания вакансии — для авто-генерации письма (читаем до клика отклика).
    description = ""
    try:
        desc_el = page.query_selector(selectors.VACANCY_DESCRIPTION)
        if desc_el:
            description = desc_el.inner_text() or ""
    except Exception:  # noqa: BLE001
        pass

    if _has_captcha(page):
        vacancy.note = "капча — пройдите вручную и продолжите"
        return STATUS_ERROR

    if page.query_selector(selectors.ALREADY_RESPONDED):
        vacancy.note = "уже откликались на сайте"
        return STATUS_SKIPPED

    respond_btn = page.query_selector(selectors.RESPOND_BUTTON)
    if respond_btn is None:
        vacancy.note = "кнопка отклика не найдена"  # внешние вакансии (Пятёрочка)
        return STATUS_SKIPPED

    antiban.human_click(page, respond_btn)  # подвод курсора кривой + клик
    page.wait_for_timeout(_WAIT_ACTION)

    if _has_captcha(page):
        vacancy.note = "капча после клика — пройдите вручную"
        return STATUS_ERROR

    if page.query_selector(selectors.RESPONSE_QUESTIONNAIRE):
        vacancy.note = "требуется тест/анкета — пропуск"
        return STATUS_SKIPPED

    _dismiss_popup(page)
    _select_resume(page, crit.resume_name, log)  # выбрать нужное резюме, если их несколько

    # Подтверждение отклика (создаётся самим кликом «Откликнуться») ДО доставки
    # письма: фолбэк-чат уводит на страницу «Отклики», поэтому сперва убеждаемся,
    # что отклик принят, читая основной DOM вакансии.
    responded = False
    for _ in range(8):  # до ~4 c
        if page.query_selector(selectors.ALREADY_RESPONDED) is not None:
            responded = True
            break
        if page.query_selector(selectors.RESPOND_BUTTON) is None:
            responded = True  # кнопка исчезла — отклик принят, ссылка дорисуется
            break
        page.wait_for_timeout(_WAIT_TOGGLE)
    if not responded:
        vacancy.note = "отклик не подтвердился"
        return STATUS_ERROR

    # Письмо: авто-генерация под вакансию из её описания (без API) либо статичный текст.
    if getattr(crit, "auto_letter", False):
        letter = letter_mod.build_letter(vacancy, description, crit)
        log("  Сгенерировал письмо под эту вакансию.")
    else:
        letter = (crit.cover_letter or "").strip()
    # Гибрид доставки письма: inline-поле на странице, иначе — сообщением в чат.
    letter_method = _deliver_cover_letter(page, vacancy.vacancy_id, letter, log)

    # Статус не зависит от судьбы письма — отклик уже создан, письмо вторично.
    if not letter:
        vacancy.note = "отклик без письма (шаблон пуст)"
    elif letter_method == "inline":
        vacancy.note = "с сопроводительным письмом"
    elif letter_method == "chat":
        vacancy.note = "письмо отправлено в чат"
    else:
        vacancy.note = "письмо не доставлено (отклик есть)"
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
            # запоминаем для дедупликации (source='sync', не считаем в лимит).
            if "уже откликались" in vacancy.note:
                storage.mark_applied(vacancy.vacancy_id, vacancy.title, vacancy.company,
                                     source="sync")
            log(f"  – {status}: {vacancy.note}")
        on_update(vacancy)

        # Человекоподобная пауза только после успешного отклика.
        if status == STATUS_APPLIED:
            pause = random.uniform(delay_min, delay_max)
            log(f"  Пауза {pause:.0f} c…")
            _interruptible_sleep(pause, should_stop)

    return applied_count
