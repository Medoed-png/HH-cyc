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
    site_id = "avito"
    display_name = "Avito Работа"
    BASE = "https://www.avito.ru"
    SEARCH_URL = "https://www.avito.ru/all/vakansii"
    QUERY_PARAM = "q"
    PAGE_PARAM = "p"
    PAGE_BASE = 1
    LOGIN_URL = "https://www.avito.ru/#login"
    ID_RE = r"_(\d+)(?:\?|$)"
    # ⚠️ CARD/TITLE_LINK/COMPANY/SALARY — заполнить при живой сверке (у Avito
    # сильная анти-бот защита, потребуется аккуратность).


class HabrCareerAdapter(GenericSiteAdapter):
    site_id = "habr"
    display_name = "Habr Career"
    BASE = "https://career.habr.com"
    SEARCH_URL = "https://career.habr.com/vacancies"
    QUERY_PARAM = "q"
    PAGE_PARAM = "page"
    PAGE_BASE = 1
    LOGIN_URL = "https://career.habr.com/users/sign_in"
    ID_RE = r"/vacancies/(\d+)"


class RabotaRuAdapter(GenericSiteAdapter):
    site_id = "rabota"
    display_name = "Rabota.ru"
    BASE = "https://www.rabota.ru"
    SEARCH_URL = "https://www.rabota.ru/vacancy"
    QUERY_PARAM = "query"
    PAGE_PARAM = "page"
    PAGE_BASE = 1
    LOGIN_URL = "https://www.rabota.ru/login"
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
