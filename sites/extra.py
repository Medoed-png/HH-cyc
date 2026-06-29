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
    site_id = "trudvsem"
    display_name = "Работа России"
    BASE = "https://trudvsem.ru"
    SEARCH_URL = "https://trudvsem.ru/vacancies"
    QUERY_PARAM = "text"
    PAGE_PARAM = "page"
    PAGE_BASE = 1
    LOGIN_URL = "https://trudvsem.ru"
    ID_RE = r"/vacancy/([\w-]+)"
