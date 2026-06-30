"""Каркасы адаптеров популярных сайтов поиска работы.

Заданы id/название и (best-effort) URL поиска/входа. Селекторы выдачи (CARD/
TITLE_LINK) пока пустые — поэтому поиск честно сообщает «нужна сверка» и не
выдаёт мусор. Каждый сайт доводим до рабочего ОТДЕЛЬНО, со сверкой селекторов на
живой странице (как делали для hh.ru): заполняем CARD/TITLE_LINK/COMPANY/SALARY,
затем поток входа и отклика. ⚠️ Все значения ниже не сверены вживую.
"""
from __future__ import annotations

from typing import Callable

from .base import (
    LoginMethod, LoginResult, LoginStatus,
    login_method_email, login_method_phone,
    login_method_manual, login_method_external,
)
from .generic import GenericSiteAdapter

Log = Callable[[str], None]


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
    icon_label = "AV"
    icon_color = "#00aaff"
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

    # Из-за анти-бота авто-вход непрактичен — только ручной вход в окне.
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_manual()]

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
    icon_label = "HC"
    icon_color = "#3b5266"
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

    # Вход идёт через единый Habr ID на account.habr.com (форма email+пароль),
    # затем редирект обратно на career.habr.com. Сверено вживую (2026-06-30).
    _EMAIL_SEL = ('form#ident-form input[name="email"], '
                  'input[type="email"][name="email"]')
    _PASS_SEL = ('form#ident-form input[name="password"], '
                 'input[type="password"][name="password"]')
    _SUBMIT_SEL = ('form#ident-form button[type="submit"], '
                   'button.button_primary[type="submit"]')
    _ERROR_SEL = '.form__error, .alert, [class*="error"], [class*="invalid"]'

    # Habr Career: email+пароль (или OAuth — вручную).
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_email(), login_method_manual()]

    def _has(self, page, selector: str) -> bool:
        try:
            return page.locator(selector).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _captcha_blocking(self, page) -> bool:
        """Капча реально требуется (Habr ID показывает «Необходимо пройти капчу»)."""
        try:
            return "пройти капчу" in (page.inner_text("body") or "").lower()
        except Exception:  # noqa: BLE001
            return False

    def _pass_captcha(self, page, log: Log = lambda m: None) -> bool:
        """Best-effort клик по чекбоксу Yandex SmartCaptcha («Я не робот»).

        В stealth-контексте чекбокс проходится без challenge-картинок (сверено
        вживую). Если появится картинка-головоломка — пройти автоматически нельзя,
        пользователь дожимает вход через «Войти вручную в окне».
        """
        try:
            for fr in page.frames:
                u = fr.url or ""
                if "smartcaptcha" in u and "checkbox" in u:
                    try:
                        fr.locator("body").click(timeout=2500)
                        page.wait_for_timeout(1500)
                        log("Отметил капчу «Я не робот».")
                        return True
                    except Exception:  # noqa: BLE001
                        return False
        except Exception:  # noqa: BLE001
            pass
        return False

    def is_logged_in(self, page) -> bool:
        """Залогинен, если на career.habr.com нет ссылки «Войти» (/users/sign_in)."""
        if page is None:
            return False
        try:
            page.goto(self.BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            if "/users/sign_in" in page.url or "account.habr.com" in page.url:
                return False
            return self._has(page, 'a[href*="/users/sign_in"]') is False and (
                self._has(page, 'a[href*="/users/sign_out"]')
                or self._has(page, '.user-menu, [class*="user-menu"], .username'))
        except Exception:  # noqa: BLE001
            return False

    def _classify(self, page) -> LoginResult:
        url = page.url
        # Успешный вход через Habr ID возвращает на career.habr.com.
        if "career.habr.com" in url and "sign_in" not in url:
            return LoginResult(LoginStatus.OK, "Вход в Habr Career выполнен.")
        if self._captcha_blocking(page):
            return LoginResult(LoginStatus.CAPTCHA_REQUIRED,
                               "Habr требует пройти капчу — нажмите «Войти вручную "
                               "в окне» и поставьте галочку «Я не робот».")
        # Остались на форме (account.habr.com) — ищем сообщение об ошибке.
        try:
            for sel in self._ERROR_SEL.split(", "):
                loc = page.locator(sel)
                for i in range(min(loc.count(), 5)):
                    t = (loc.nth(i).inner_text() or "").strip()
                    if t and any(k in t.lower() for k in
                                 ("невер", "не найд", "парол", "ошиб", "incorrect")):
                        return LoginResult(LoginStatus.BAD_CREDENTIALS, t[:160])
        except Exception:  # noqa: BLE001
            pass
        if "account.habr.com" in url:
            return LoginResult(LoginStatus.BAD_CREDENTIALS,
                               "Неверный e-mail или пароль Habr.")
        return LoginResult(LoginStatus.FAILED, "Не удалось подтвердить вход в Habr.")

    def login_with_credentials(self, page, username: str, password: str,
                               log: Log = lambda m: None) -> LoginResult:
        page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if not self._has(page, self._EMAIL_SEL):
            # форма не появилась — возможно, уже вошли
            if "sign_in" not in page.url and "account.habr.com" not in page.url:
                return LoginResult(LoginStatus.OK, "Похоже, вход уже выполнен.")
            return LoginResult(LoginStatus.FAILED,
                               "Форма входа Habr не найдена — войдите вручную в окне.")
        if not password:
            return LoginResult(LoginStatus.BAD_CREDENTIALS,
                               "Для Habr нужен пароль (вход по коду не поддерживается).")
        try:
            page.fill(self._EMAIL_SEL, username)
            page.fill(self._PASS_SEL, password)
        except Exception as e:  # noqa: BLE001
            return LoginResult(LoginStatus.FAILED, f"Не удалось заполнить форму: {e}")
        # Habr ID защищён Yandex SmartCaptcha — отмечаем «Я не робот» до отправки.
        self._pass_captcha(page, log)
        log("Отправляю форму входа Habr…")
        try:
            page.click(self._SUBMIT_SEL)
        except Exception:  # noqa: BLE001
            page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        return self._classify(page)


class RabotaRuAdapter(GenericSiteAdapter):
    """Rabota.ru. Поиск сверен вживую (2026-06-30).

    Поиск публичный. Отклик требует входа+анкеты — пока заглушка. Регион не
    фильтруется (по умолчанию выдача по Москве/всей базе).
    """
    site_id = "rabota"
    display_name = "Rabota.ru"
    icon_label = "RR"
    icon_color = "#ff5a3c"
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

    # Rabota.ru: email/телефон+пароль (или соцсети — вручную). Вход — со сверкой.
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_email(), login_method_phone(), login_method_manual()]


class TrudvsemAdapter(GenericSiteAdapter):
    """Работа России (trudvsem.ru). Поиск сверен вживую (2026-06-30).

    Поиск публичный (без входа). Отклик требует входа через Госуслуги (ЕСИА) —
    автоматизировать непрактично, поэтому отклик пока остаётся заглушкой
    (run_applications из GenericSiteAdapter). Регион пока не фильтруется (поиск по
    всей РФ) — маппинг города в код _regionIds можно добавить позже.
    """
    site_id = "trudvsem"
    display_name = "Работа России"
    icon_label = "РР"
    icon_color = "#0066cc"
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

    # Работа России: вход только через Госуслуги (ЕСИА) — авто-вход непрактичен.
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_external("Госуслуги")]
