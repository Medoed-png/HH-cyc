"""Адаптер сайта hh.ru.

На этапе M1 адаптер делегирует в существующие модули `hh_bot` (search/applier/
responses/suggest/selectors), консолидируя hh-специфику за единым интерфейсом
SiteAdapter. Физический перенос этих модулей внутрь sites/hh/ — последующая
косметическая правка; сейчас важнее сам шов: Worker общается только с адаптером.
"""
from __future__ import annotations

import re
from typing import Callable

from playwright.sync_api import Page

from hh_bot import antiban
from hh_bot import selectors
from hh_bot import search as _search
from hh_bot import applier as _applier
from hh_bot import responses as _responses
from hh_bot.suggest import fetch_suggestions
from hh_bot.cities_list import CITIES
from hh_bot.config import Criteria
from hh_bot.models import Vacancy
from hh_bot.storage import Storage

from ..base import (
    SiteAdapter, LoginResult, LoginStatus,
    LoginMethod, login_method_phone, login_method_email,
)

Log = Callable[[str], None]

# Крупные города — выше в подсказках (перенесено из web/server.py).
_MAJOR_CITIES = {
    "москва", "санкт-петербург", "новосибирск", "екатеринбург", "казань",
    "нижний новгород", "челябинск", "самара", "омск", "ростов-на-дону",
    "уфа", "красноярск", "краснодар", "воронеж", "пермь", "волгоград", "россия",
}


class HHAdapter(SiteAdapter):
    site_id = "hh"
    display_name = "hh.ru"
    icon_label = "hh"
    icon_color = "#d6001c"

    @property
    def base_url(self) -> str:
        return selectors.BASE

    # --- способы входа (серверный вход реализован: телефон/почта) ---
    def login_methods(self) -> list[LoginMethod]:
        return [login_method_phone(), login_method_email()]

    # --- авторизация ---
    def is_logged_in(self, page: Page) -> bool:
        """Залогинен ли пользователь.

        hh.ru БОЛЬШЕ НЕ редиректит гостя с /applicant/resumes на /account/login —
        отдаёт «гостевую» версию страницы. Поэтому проверки только по URL мало
        (ложный «вошёл» при истёкшей сессии → отклики/ответы пустые). Проверяем
        наличие маркера личного кабинета в шапке (меню профиля/резюме).
        """
        if page is None:
            return False
        page.goto(selectors.BASE + "/applicant/resumes", wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        if "/account/login" in page.url or "/auth/" in page.url:
            return False
        try:
            return page.locator(selectors.LOGGED_IN_MARKER).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def open_manual_login(self, page: Page) -> None:
        page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        # Устаревшие cookies редиректят /account/login на главную — форма входа не
        # открывается; чистим cookies и открываем страницу входа заново.
        if (not self._has(page, selectors.LOGIN_SUBMIT)
                and "/account/login" not in page.url):
            try:
                page.context.clear_cookies()
            except Exception:  # noqa: BLE001
                pass
            page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")

    # --- серверный логин по логину/паролю + код (M5b) ---
    def _logged_in_now(self, page: Page) -> bool:
        """Признак входа на ТЕКУЩЕЙ странице без навигации (после submit)."""
        if "/account/login" in page.url or "/auth/" in page.url:
            return False
        try:
            return page.locator(selectors.LOGGED_IN_MARKER).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _has(self, page: Page, selector: str) -> bool:
        try:
            return page.locator(selector).first.is_visible(timeout=1500)
        except Exception:  # noqa: BLE001
            return False

    def _click(self, page: Page, selector: str, force: bool = False,
               timeout: int = 5000) -> bool:
        try:
            page.locator(selector).first.click(force=force, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def login_with_credentials(self, page: Page, username: str, password: str,
                               log: Log = lambda m: None) -> LoginResult:
        """Серверный вход на hh.ru по логину/паролю (многошаговая форма magritte).

        Шаги: [Войти] -> выбор Почта/Телефон + логин -> [Войти с паролем] ->
        пароль -> [Войти] -> классификация (OK / код / капча / неверные / ошибка).
        username с «@» трактуется как email, иначе как телефон.
        """
        page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if self._logged_in_now(page):
            log("Уже авторизованы на hh.ru.")
            return LoginResult(LoginStatus.OK)

        # Устаревшие cookies заставляют hh редиректить /account/login на главную —
        # форма входа не открывается («не нашёл кнопку Войти»). Чистим cookies и
        # открываем страницу входа заново.
        if not self._has(page, selectors.LOGIN_SUBMIT):
            log("Форма входа не открылась — сбрасываю старую сессию hh.ru…")
            try:
                page.context.clear_cookies()
            except Exception:  # noqa: BLE001
                pass
            page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        # Закрыть баннер cookie, если он перекрывает кнопки.
        self._click(page, '[data-qa="cookies-policy-informer-accept"]', timeout=2000)

        # Шаг 1: тип «соискатель» уже выбран по умолчанию; жмём «Войти»
        # (обычный клик, при перекрытии оверлеем — форс-клик).
        if not (self._click(page, selectors.LOGIN_SUBMIT)
                or self._click(page, selectors.LOGIN_SUBMIT, force=True)):
            log("Не нашёл кнопку «Войти» на форме hh.ru (проверьте селекторы).")
            return LoginResult(LoginStatus.FAILED, "нет кнопки входа")
        page.wait_for_timeout(1500)

        # Шаг 2: выбрать способ и ввести логин.
        is_email = "@" in username
        if is_email:
            self._click(page, selectors.LOGIN_CRED_EMAIL, force=True)  # radio под оверлеем
            page.wait_for_timeout(700)
            if not self._has(page, selectors.LOGIN_EMAIL_INPUT):
                log("Поле e-mail не появилось на форме входа hh.ru.")
                return LoginResult(LoginStatus.FAILED, "нет поля логина (email)")
            antiban.human_type(page, page.locator(selectors.LOGIN_EMAIL_INPUT).first, username)
        else:
            self._click(page, selectors.LOGIN_CRED_PHONE, force=True)
            page.wait_for_timeout(500)
            digits = re.sub(r"\D", "", username)
            if digits[:1] in ("7", "8"):  # код страны вводится отдельным полем (+7)
                digits = digits[1:]
            if not self._has(page, selectors.LOGIN_PHONE_INPUT):
                log("Поле телефона не появилось на форме входа hh.ru.")
                return LoginResult(LoginStatus.FAILED, "нет поля логина (телефон)")
            antiban.human_type(page, page.locator(selectors.LOGIN_PHONE_INPUT).first, digits)
        page.wait_for_timeout(500)

        # Шаг 3: ВХОД ПО ПАРОЛЮ (пароль задан) ИЛИ ПО КОДУ (пароль пуст).
        if (password or "").strip():
            # Переключиться на ввод пароля (появляется после ввода логина).
            self._click(page, selectors.LOGIN_BY_PASSWORD_LINK)
            page.wait_for_timeout(1200)
            if not self._has(page, selectors.LOGIN_PASSWORD_INPUT):
                log("Поле пароля не появилось — возможно, hh запросил код.")
                return self._classify_login_state(page, log)
            antiban.human_type(
                page, page.locator(selectors.LOGIN_PASSWORD_INPUT).first, password
            )
            self._click(page, selectors.LOGIN_SUBMIT)  # «Войти»
        else:
            # Пароль не задан — запрашиваем код: жмём «Дальше» (hh пришлёт SMS/письмо).
            log("Пароль не задан — вхожу по коду из SMS/письма.")
            # Пауза «по-человечески» перед запросом кода — реже триггерит анти-бот/капчу.
            antiban.human_pause(0.8, 2.0)
            self._click(page, selectors.LOGIN_SUBMIT)  # «Дальше»
        page.wait_for_timeout(2800)
        return self._classify_login_state(page, log)

    def _code_step_present(self, page: Page) -> bool:
        """Виден ли шаг ввода кода (pincode-поле или его обёртка)."""
        return (self._has(page, selectors.LOGIN_CODE_INPUT)
                or self._has(page, selectors.LOGIN_CODE_WRAPPER))

    def submit_sms_code(self, page: Page, code: str,
                        log: Log = lambda m: None) -> LoginResult:
        """Ввести код (pincode) на странице, оставленной login_with_credentials.

        Поле кода — magritte-pincode: фокусируем кликом и вводим цифры с клавиатуры;
        форма отправляется автоматически по вводу всех цифр (submit-кнопки нет).
        """
        if not self._code_step_present(page):
            if self._logged_in_now(page):  # вход мог уже завершиться
                return LoginResult(LoginStatus.OK)
            return LoginResult(LoginStatus.FAILED, "поле кода не найдено")
        digits = re.sub(r"\D", "", code)
        try:
            # Фокус: клик по полю кода (или обёртке, если поле визуально скрыто).
            for sel in (selectors.LOGIN_CODE_INPUT, selectors.LOGIN_CODE_WRAPPER):
                if self._has(page, sel):
                    self._click(page, sel, force=True)
                    break
            for ch in digits:
                page.keyboard.type(ch)
                page.wait_for_timeout(120)
        except Exception:  # noqa: BLE001
            return LoginResult(LoginStatus.FAILED, "не удалось ввести код")
        page.wait_for_timeout(2500)  # автоотправка pincode
        self._click(page, selectors.LOGIN_CODE_SUBMIT)  # no-op, если кнопки нет

        # После верного кода hh делает редирект — он не мгновенный. Ждём итог:
        # либо появился маркер входа, либо ушли со страницы логина, либо ошибка/капча.
        for _ in range(16):  # до ~8 секунд
            page.wait_for_timeout(500)
            if self._logged_in_now(page):
                break
            if "/account/login" not in page.url and "/auth/" not in page.url:
                break
            if self._captcha_present(page) or self._has(page, selectors.LOGIN_ERROR):
                break

        result = self._classify_login_state(page, log)
        # Подстраховка: вход мог пройти, но маркер не успел отрисоваться на текущей
        # (переходной) странице — перепроверяем полноценной навигацией.
        if result.status == LoginStatus.FAILED:
            try:
                if self.is_logged_in(page):
                    log("Вход на hh.ru выполнен.")
                    return LoginResult(LoginStatus.OK)
            except Exception:  # noqa: BLE001
                pass
            self._log_login_state(page, log)  # диагностика «неизвестного» состояния
        return result

    def _log_login_state(self, page: Page, log: Log) -> None:
        """Диагностика непонятного состояния формы входа (для лога)."""
        try:
            url = page.url[:90]
            btns = page.eval_on_selector_all(
                "button,[role=button]",
                "els=>els.filter(e=>e.offsetParent).map(e=>(e.getAttribute('data-qa')"
                "||(e.innerText||'').trim().slice(0,24))).filter(Boolean).slice(0,10)")
            errs = page.eval_on_selector_all(
                '[data-qa*="error"]',
                "els=>els.filter(e=>e.offsetParent).map(e=>(e.innerText||'')"
                ".trim().slice(0,80)).filter(Boolean).slice(0,5)")
            log(f"[диагностика входа] url={url} | кнопки={btns} | ошибки={errs}")
        except Exception:  # noqa: BLE001
            pass

    def _captcha_present(self, page: Page) -> bool:
        """Капча на форме входа (по data-qa-маркерам или по тексту страницы)."""
        if self._has(page, selectors.CAPTCHA):
            return True
        try:
            body = (page.inner_text("body") or "").lower()
            return any(t in body for t in selectors.CAPTCHA_TEXT)
        except Exception:  # noqa: BLE001
            return False

    def _classify_login_state(self, page: Page, log: Log) -> LoginResult:
        """Определить итог попытки входа по текущему состоянию страницы."""
        if self._logged_in_now(page):
            log("Вход на hh.ru выполнен.")
            return LoginResult(LoginStatus.OK)
        # Капчу проверяем ДО ошибки логина: hh показывает «Текст с картинки» как
        # form-helper-error, иначе её ошибочно приняли бы за неверный логин/пароль.
        if self._captcha_present(page):
            log("hh.ru показал капчу «Текст с картинки» — её нужно пройти вручную: "
                "нажмите «Войти вручную в окне», решите капчу и введите телефон/код.")
            return LoginResult(LoginStatus.CAPTCHA_REQUIRED, "требуется капча")
        if self._code_step_present(page):
            log("hh.ru запросил код подтверждения (SMS/письмо).")
            return LoginResult(LoginStatus.SMS_REQUIRED)
        if self._has(page, selectors.LOGIN_ERROR):
            log("hh.ru отклонил вход — проверьте логин и пароль.")
            return LoginResult(LoginStatus.BAD_CREDENTIALS)
        log("Не удалось определить результат входа на hh.ru.")
        return LoginResult(LoginStatus.FAILED, "неизвестное состояние формы входа")

    # --- поиск ---
    def search(self, page: Page, query: str, region: str, max_pages: int,
               log: Log = lambda m: None, experience: str = "",
               employment: list | None = None, schedule: list | None = None,
               should_stop: Callable[[], bool] = lambda: False
               ) -> list[Vacancy]:
        area = self.map_region(region)  # регион приходит именем города -> id области hh
        found = _search.search(page, query, area, max_pages, log=log,
                               experience=experience, employment=employment,
                               schedule=schedule, should_stop=should_stop)
        for v in found:
            v.site = self.site_id
        return found

    # --- отклик ---
    def run_applications(self, page: Page, vacancies: list[Vacancy], crit: Criteria,
                         storage: Storage, log: Log = lambda m: None,
                         should_stop: Callable[[], bool] = lambda: False,
                         on_update: Callable[[Vacancy], None] = lambda v: None) -> int:
        return _applier.run_applications(page, vacancies, crit, storage, log=log,
                                         should_stop=should_stop, on_update=on_update)

    # --- описание вакансии (для генерации письма; вход не нужен) ---
    def fetch_description(self, page: Page, url: str) -> str:
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            el = page.query_selector(selectors.VACANCY_DESCRIPTION)
            text = el.inner_text() if el else page.inner_text("body")
            return (text or "")[:8000]
        except Exception:  # noqa: BLE001
            return ""

    # --- ответы / чат ---
    def fetch_responses(self, page: Page, log: Log = lambda m: None) -> dict:
        result = _responses.fetch_responses(page, log=log)
        for it in result.get("items", []):
            it["site"] = self.site_id  # элементы самоописываемы (нужно для режима «все»)
        return result

    def fetch_chat(self, page: Page, vacancy_id: str,
                   log: Log = lambda m: None) -> list:
        return _responses.fetch_chat(page, vacancy_id, log=log)

    # --- таксономия / автоподсказки ---
    def map_region(self, city_name: str) -> str:
        """Название города -> id области hh.ru ("113" = вся Россия по умолчанию)."""
        name = str(city_name or "").strip()
        if not name:
            return "113"
        if name.isdigit():  # уже id области (старые сохранённые конфиги)
            return name
        city_id = CITIES.get(name)
        if city_id is None:
            low = {k.lower(): v for k, v in CITIES.items()}
            city_id = low.get(name.lower(), "113")
        return str(city_id)

    def suggest_professions(self, text: str) -> list:
        return fetch_suggestions(text)

    def suggest_cities(self, query: str) -> list:
        q = (query or "").strip().lower()
        if not q:
            return []

        def rank(c):
            return (c.lower() not in _MAJOR_CITIES, len(c), c)

        starts = sorted((c for c in CITIES if c.lower().startswith(q)), key=rank)
        contains = sorted(
            (c for c in CITIES if q in c.lower() and not c.lower().startswith(q)),
            key=rank,
        )
        return (starts + contains)[:10]

    # --- схема формы критериев ---
    def config_schema(self) -> list[dict]:
        from ..base import ConfigField
        from dataclasses import asdict
        fields = [
            ConfigField("professions", "Профессии (через запятую)", "text",
                        "python разработчик, backend"),
            ConfigField("region", "Город / регион", "city", "Москва"),
            ConfigField("salary_min", "Зарплата от", "number", "150000"),
            ConfigField("exclude_words", "Исключающие слова", "text", "1с, php"),
            ConfigField("include_words", "Обязательные слова", "text"),
            ConfigField("resume_name", "Резюме (имя)", "text"),
            ConfigField("daily_limit", "Дневной лимит откликов", "number", "100"),
            ConfigField("max_pages", "Страниц выдачи", "number", "3"),
            ConfigField("cover_letter", "Сопроводительное письмо", "textarea"),
        ]
        return [asdict(f) for f in fields]
