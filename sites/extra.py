"""Каркасы адаптеров популярных сайтов поиска работы.

Заданы id/название и (best-effort) URL поиска/входа. Селекторы выдачи (CARD/
TITLE_LINK) пока пустые — поэтому поиск честно сообщает «нужна сверка» и не
выдаёт мусор. Каждый сайт доводим до рабочего ОТДЕЛЬНО, со сверкой селекторов на
живой странице (как делали для hh.ru): заполняем CARD/TITLE_LINK/COMPANY/SALARY,
затем поток входа и отклика. ⚠️ Все значения ниже не сверены вживую.
"""
from __future__ import annotations

from .generic import GenericSiteAdapter


class AvitoAdapter(GenericSiteAdapter):
    """Avito Работа. ⚠️ У Avito сильная анти-бот защита: с дата-центрового IP сайт
    отдаёт страницу «Доступ ограничен: проблема с IP» с капчей (проверено вживую
    2026-06-30 — поиск не открылся). Чтобы поиск заработал, нужен российский
    residential/mobile-прокси (поле «Прокси») и/или ручное прохождение капчи через
    «Показать окно браузера». Селекторы ниже — best-effort по типичной разметке
    Avito (data-marker), НЕ сверены вживую (страница была заблокирована).
    """
    site_id = "avito"
    display_name = "Avito Работа"
    BASE = "https://www.avito.ru"
    SEARCH_URL = "https://www.avito.ru/all/vakansii"
    QUERY_PARAM = "q"
    PAGE_PARAM = "p"
    PAGE_BASE = 1
    LOGIN_URL = "https://www.avito.ru/#login"
    # best-effort (Avito помечает элементы data-marker); сверить при доступе через прокси.
    CARD = "[data-marker='item']"
    TITLE_LINK = "[data-marker='item-title']"
    SALARY = "[data-marker='item-price']"
    ID_RE = r"_(\d+)(?:\?|$)"
    BLOCK_MSG = ("Avito ограничил доступ (капча/IP). Нужен российский residential-"
                 "прокси (поле «Прокси») и/или пройдите капчу через «Показать окно "
                 "браузера».")

    def _blocked(self, page) -> bool:
        try:
            title = (page.title() or "").lower()
            if "доступ ограничен" in title or "captcha" in title:
                return True
            body = (page.inner_text("body") or "").lower()[:300]
            return any(k in body for k in ("доступ ограничен", "решения капчи",
                                           "проблема с ip"))
        except Exception:  # noqa: BLE001
            return False


class HabrCareerAdapter(GenericSiteAdapter):
    """Habr Career (career.habr.com). Поиск сверен вживую (2026-06-30).

    Поиск публичный. Отклик требует входа и анкеты — отдельная доводка (пока
    run_applications из GenericSiteAdapter = заглушка). Регион пока не фильтруется.
    """
    site_id = "habr"
    display_name = "Habr Career"
    BASE = "https://career.habr.com"
    # type=all — все вакансии (не только подходящие под профиль); q — запрос.
    SEARCH_URL = "https://career.habr.com/vacancies?type=all"
    QUERY_PARAM = "q"
    PAGE_PARAM = "page"
    PAGE_BASE = 1
    LOGIN_URL = "https://career.habr.com/users/sign_in"
    CARD = ".vacancy-card"
    TITLE_LINK = ".vacancy-card__title-link"
    COMPANY = ".vacancy-card__company"
    SALARY = ".vacancy-card__salary"
    ID_RE = r"/vacancies/(\d+)"


class RabotaRuAdapter(GenericSiteAdapter):
    """Rabota.ru. Поиск сверен вживую (2026-06-30).

    Поиск публичный. Отклик требует входа+анкеты — пока заглушка. Регион не
    фильтруется (по умолчанию выдача по Москве/всей базе).
    """
    site_id = "rabota"
    display_name = "Rabota.ru"
    BASE = "https://www.rabota.ru"
    SEARCH_URL = "https://www.rabota.ru/vacancy/"
    QUERY_PARAM = "query"
    PAGE_PARAM = "page"
    PAGE_BASE = 1
    LOGIN_URL = "https://www.rabota.ru/login"
    CARD = ".vacancy-preview-card"
    TITLE_LINK = ".vacancy-preview-card__title_border"
    COMPANY = ".vacancy-preview-card__company-name"
    SALARY = ".vacancy-preview-card__salary"
    ID_RE = r"/vacancy/(\d+)"


class TrudvsemAdapter(GenericSiteAdapter):
    """Работа России (trudvsem.ru). Поиск сверен вживую (2026-06-30).

    Поиск публичный (без входа). Отклик требует входа через Госуслуги (ЕСИА) —
    автоматизировать непрактично, поэтому отклик пока остаётся заглушкой
    (run_applications из GenericSiteAdapter). Регион пока не фильтруется (поиск по
    всей РФ) — маппинг города в код _regionIds можно добавить позже.
    """
    site_id = "trudvsem"
    display_name = "Работа России"
    BASE = "https://trudvsem.ru"
    SEARCH_URL = "https://trudvsem.ru/vacancy/search"
    QUERY_PARAM = "_title"
    PAGE_PARAM = "page"
    PAGE_BASE = 0
    LOGIN_URL = "https://trudvsem.ru"
    # Сверено: карточка результата, ссылка /vacancy/card/<company>/<uuid>,
    # заголовок и зарплата — отдельными элементами; компания в карточке списка
    # обычно отсутствует.
    CARD = ".search-results-simple-card"
    TITLE_LINK = "a[href*='/vacancy/card/']"
    TITLE = ".search-results-simple-card__name"
    SALARY = ".search-results-simple-card__salary"
    ID_RE = r"/vacancy/card/[^/]+/([0-9a-f-]+)"
