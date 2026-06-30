"""Сессия браузера одного пользователя на одном сайте.

Обобщение прежнего Worker: владеет своим Playwright-браузером (persistent-профиль
profiles/{user_id}/{site_id}/), своим хранилищем истории (Storage с тем же
скоупом) и адаптером сайта. Playwright sync-API нельзя дёргать из разных потоков,
поэтому каждая сессия живёт в своём выделенном потоке; общение — через очереди
команд и событий. Пулом сессий управляет SessionManager.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from hh_bot.browser import Browser
from hh_bot.config import Criteria
from hh_bot import antiban
from hh_bot import filters
from hh_bot import credentials
from hh_bot import notify
from hh_bot.storage import Storage
from sites import get_adapter
from sites.base import LoginStatus

# Типы событий, отправляемых в UI (через SessionManager -> per-user SSE).
EV_LOG = "log"              # payload: str
EV_LOGIN = "login"          # payload: bool (залогинен ли)
EV_VACANCY = "vacancy"      # payload: Vacancy (новая/обновлённая)
EV_RESPONSES = "responses"  # payload: dict {items, unread}
EV_CHAT = "chat"            # payload: dict {vacancy_id, messages}
EV_CONN = "conn_status"     # payload: dict (credentials.status: статус подключения аккаунта)
EV_DONE = "done"            # payload: str (что завершилось)

# Корень для пользовательских профилей браузера.
_PROFILES_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")


class BrowserSession(threading.Thread):
    """Поток-исполнитель команд браузера для пары (user_id, site_id)."""

    def __init__(self, user_id: int, site_id: str = "hh"):
        super().__init__(daemon=True)
        self.user_id = user_id
        self.site_id = site_id
        self.commands: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self._stop_apply = threading.Event()
        self._browser: Browser | None = None
        self._running = True
        self._last_suitable: list | None = None  # найденные вакансии для откликов
        self.adapter = get_adapter(site_id)
        self._storage = Storage(user_id=user_id, site_id=self.adapter.site_id)
        self._profile_dir = os.path.join(_PROFILES_ROOT, str(user_id), self.adapter.site_id)
        self.last_activity = time.monotonic()

    # --- API для сервера (потокобезопасно через очередь) ---
    def submit(self, name: str, **kwargs) -> None:
        self.touch()
        self.commands.put((name, kwargs))

    def request_stop_apply(self) -> None:
        self._stop_apply.set()

    def shutdown(self) -> None:
        self.commands.put(("quit", {}))

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    # --- внутреннее ---
    def _log(self, msg: str) -> None:
        print(f"[bot u{self.user_id}/{self.site_id}]", msg, flush=True)
        self.events.put((EV_LOG, msg))

    def _proxy_url(self) -> str | None:
        """Прокси для этой сессии: персональный из БД (users.proxy_url_enc),
        иначе глобальный из env HH_PROXY_URL. Реальный пул прокси — в облаке (M8)."""
        try:
            personal = credentials.get_proxy(self.user_id)
        except Exception:  # noqa: BLE001 — БД недоступна не должна ронять запуск
            personal = ""
        return personal or os.environ.get("HH_PROXY_URL", "").strip() or None

    def _ensure_browser(self, visible: bool = False) -> Browser:
        # Браузер мог быть закрыт пользователем (закрыл окно) или упасть —
        # тогда пересоздаём, иначе page.goto падает с "target closed".
        if self._browser is not None and not self._browser.is_alive():
            self._log("Окно браузера было закрыто — открываю заново.")
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        # Для ручного входа нужно видимое окно; если сейчас браузер невидимый —
        # пересоздаём его с окном. Обычная работа идёт в фоне (headless).
        if self._browser is not None and visible and self._browser.headless:
            self._log("Открываю видимое окно браузера для входа…")
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._browser is None:
            # visible -> окно с интерфейсом; иначе режим по умолчанию (невидимый).
            self._browser = Browser(
                headless=False if visible else None,
                user_data_dir=self._profile_dir,
                proxy_url=self._proxy_url(),
            )
            self._browser.start()
            antiban.session_start_jitter()  # разнести одновременные старты сессий
        return self._browser

    def _persist_cookies(self) -> None:
        """Сохранить cookies сразу (после подтверждённого входа), не дожидаясь close."""
        if self._browser is not None:
            self._browser._save_cookies()

    def _go_background(self) -> None:
        """Спрятать окно: если браузер видимый — закрыть его (cookies сохранятся).

        Следующая команда переоткроет браузер уже в фоне (headless) с восстановлением
        cookies, поэтому вход не теряется, а окно перестаёт мозолить глаза.
        """
        if self._browser is not None and not self._browser.headless:
            self._log("Вход подтверждён — убираю окно браузера в фон.")
            try:
                self._browser.close()  # close() сам сохраняет cookies
            except Exception:  # noqa: BLE001
                pass
            self._browser = None

    def run(self) -> None:
        while self._running:
            try:
                name, kwargs = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                handler = getattr(self, f"_cmd_{name}", None)
                if handler:
                    handler(**kwargs)
            except Exception as e:  # noqa: BLE001
                self._log(f"Ошибка: {e}")
                self.events.put((EV_DONE, name))
        if self._browser:
            self._browser.close()
        self._storage.close()

    # --- команды ---
    def _cmd_quit(self) -> None:
        self._running = False

    def _cmd_show_browser(self) -> None:
        """Показать видимое окно браузера (универсально: ручной вход + капча).

        Если пользователь НЕ авторизован — открываем страницу входа сайта (ручной
        логин, вход сохранится в cookies). Если уже авторизован — открываем главную
        для ручных действий/капчи. Заменяет прежнюю отдельную кнопку «Войти».
        """
        br = self._ensure_browser(visible=True)
        try:
            br.page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        if self.adapter.is_logged_in(br.page):
            self._persist_cookies()
            try:
                br.page.goto(self.adapter.base_url, wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001
                pass
            self._log("Окно браузера открыто. Можно пройти капчу или действия вручную.")
            self.events.put((EV_LOGIN, True))
        else:
            self.adapter.open_manual_login(br.page)
            self._log("Открыл окно на странице входа — войдите вручную, вход сохранится.")
            self.events.put((EV_LOGIN, False))
        self.events.put((EV_DONE, "show_browser"))

    # --- серверный логин по логину/паролю + код (M5b) ---
    def _emit_conn(self) -> None:
        """Отправить в UI текущий статус подключения аккаунта (без пароля)."""
        self.events.put((EV_CONN, credentials.status(self.user_id, self.site_id)))

    def _handle_login_result(self, result) -> None:
        """Обработать LoginResult: обновить статус кред, события, скрыть окно при успехе."""
        st = result.status
        if st == LoginStatus.OK:
            credentials.set_status(self.user_id, self.site_id,
                                   credentials.STATUS_CONNECTED, logged_in=True)
            self._persist_cookies()
            self._go_background()  # автологин шёл в фоне; окна и не было, но на всякий
            self.events.put((EV_LOGIN, True))
        elif st == LoginStatus.SMS_REQUIRED:
            # Страница оставлена на шаге кода; ждём команду submit_sms (не блокируем поток).
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_NEEDS_SMS)
        elif st == LoginStatus.CAPTCHA_REQUIRED:
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_NEEDS_CAPTCHA)
            self._log("Нужна капча: нажмите «Показать окно браузера» и пройдите её вручную.")
        else:  # BAD_CREDENTIALS | FAILED
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_INVALID)
            self.events.put((EV_LOGIN, False))
        self._emit_conn()

    def _cmd_logout_site(self) -> None:
        """Выйти из аккаунта сайта в сессии: сбросить cookies/профиль браузера.

        Нужно, чтобы проверить серверный вход по логину/паролю (иначе бот видит
        активную сессию по cookies и форму входа не проходит). После сброса
        check_login покажет «не вошли», и появится панель подключения.
        """
        import shutil
        cookies_path = self._profile_dir.rstrip("/\\") + ".cookies.json"
        # Сбросить cookies в живом контексте, затем закрыть браузер.
        try:
            if self._browser is not None and self._browser.is_alive():
                self._browser.context.clear_cookies()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        # Удалить сохранённые cookies и профиль на диске (иначе вход восстановится).
        try:
            if os.path.exists(cookies_path):
                os.remove(cookies_path)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self._profile_dir, ignore_errors=True)
        self._log(f"Вышли из аккаунта {self.adapter.display_name} — можно подключить другой.")
        self.events.put((EV_LOGIN, False))
        self.events.put((EV_DONE, "logout_site"))

    def _cmd_connect(self) -> None:
        """Серверный вход: берём сохранённые креды и логинимся в фоне."""
        creds = credentials.get(self.user_id, self.site_id)
        if creds is None:
            self._log("Нет сохранённых данных для входа — введите логин и пароль.")
            self._emit_conn()
            self.events.put((EV_DONE, "connect"))
            return
        br = self._ensure_browser()  # автологин идёт в фоне (headless)
        self._log(f"Вхожу на {self.adapter.display_name} как {creds.username}…")
        try:
            result = self.adapter.login_with_credentials(
                br.page, creds.username, creds.password, log=self._log
            )
        except NotImplementedError:
            self._log("Серверный логин для этого сайта не реализован.")
            self.events.put((EV_DONE, "connect"))
            return
        self._handle_login_result(result)
        self.events.put((EV_DONE, "connect"))

    def _cmd_submit_sms(self, code: str) -> None:
        """Ввести код подтверждения на странице, оставленной командой connect."""
        if self._browser is None or not self._browser.is_alive():
            self._log("Сессия входа потеряна — начните подключение заново.")
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_INVALID)
            self._emit_conn()
            self.events.put((EV_DONE, "submit_sms"))
            return
        self._log("Отправляю код подтверждения…")
        try:
            result = self.adapter.submit_sms_code(self._browser.page, code, log=self._log)
        except NotImplementedError:
            self.events.put((EV_DONE, "submit_sms"))
            return
        self._handle_login_result(result)
        self.events.put((EV_DONE, "submit_sms"))

    def _cmd_check_login(self) -> None:
        br = self._ensure_browser()
        logged = self.adapter.is_logged_in(br.page)
        if logged:
            self._persist_cookies()  # закрепить вход на диске сразу
            self._go_background()    # убрать видимое окно входа в фон
        self._log("Авторизация подтверждена." if logged else "Вы ещё не вошли.")
        self.events.put((EV_LOGIN, logged))
        self.events.put((EV_DONE, "check_login"))

    def _do_search(self, crit: Criteria) -> list:
        """Найти и отфильтровать вакансии, вывести подходящие в таблицу."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            return []
        all_found = []
        # Без профессии — один поиск с пустым запросом: сайт вернёт ВСЕ вакансии
        # по остальным фильтрам (регион, опыт, занятость, график/удалёнка).
        queries = crit.profession_texts or [""]
        for text in queries:
            antiban.rate_limit(self.user_id)  # разнести запросы во времени
            self._log(f"Поиск: {text or 'все вакансии (без профессии)'}")
            found = self.adapter.search(
                br.page, text, crit.region, crit.max_pages, log=self._log,
                experience=crit.experience, employment=crit.employment,
                schedule=crit.schedule,
            )
            all_found.extend(found)
        self._log(f"Всего найдено: {len(all_found)}")
        suitable = filters.filter_all(all_found, crit, self._storage)
        self._log(f"Подходящих по критериям: {len(suitable)}")
        for v in suitable:
            self.events.put((EV_VACANCY, v))
        self._last_suitable = suitable  # запоминаем для откликов
        return suitable

    def _cmd_search(self, crit: Criteria) -> None:
        self._do_search(crit)
        self.events.put((EV_DONE, "search"))

    def _cmd_responses(self) -> None:
        """Собрать ответы работодателей и отправить их в интерфейс."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            self.events.put((EV_DONE, "responses"))
            return
        antiban.rate_limit(self.user_id)  # не частить с запросами к hh.ru
        self._log("Загружаю ответы на отклики…")
        result = self.adapter.fetch_responses(br.page, log=self._log)
        # Заносим все текущие отклики с сайта в память, чтобы не открывать повторно.
        import re
        added = 0
        invites = []  # сменившиеся на «приглашение» — для Telegram-уведомления
        for it in result["items"]:
            m = re.search(r"/vacancy/(\d+)", it.get("url", ""))
            if not m:
                continue
            vid = m.group(1)
            if not self._storage.is_applied(vid):
                self._storage.mark_applied(vid, it["title"], it["company"],
                                           source="sync")
                added += 1
            # Записать статус отклика для аналитики; set_status вернёт прежний при
            # изменении — ловим переход в «приглашение/собеседование» для уведомления.
            status = it.get("status", "")
            if status:
                prev = self._storage.set_status(vid, status)
                low = status.lower()
                if prev is not None and any(k in low for k in
                                            ("приглаш", "собеседов", "оффер")):
                    invites.append((it.get("title", ""), it.get("company", ""), status))
        if added:
            self._log(f"Добавлено в память откликов: {added} (повторно не откликнемся).")
        if invites:
            self._notify_telegram(invites)
        self.events.put((EV_RESPONSES, result))
        self.events.put((EV_DONE, "responses"))

    def _notify_telegram(self, items: list) -> None:
        """Отправить Telegram-уведомление о новых приглашениях (если задан chat_id)."""
        chat_id = credentials.get_telegram(self.user_id)
        if not chat_id:
            return
        lines = ["🔔 Новые статусы откликов на hh.ru:"]
        for title, company, status in items[:10]:
            lines.append(f"• {status}: {title} — {company}")
        if notify.send_telegram(chat_id, "\n".join(lines)):
            self._log(f"Telegram-уведомление отправлено ({len(items)}).")

    def _cmd_chat(self, vacancy_id: str) -> None:
        """Прочитать чат одной вакансии по требованию."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сначала войдите на {self.adapter.display_name} (кнопка «Войти»).")
            self.events.put((EV_CHAT, {"vacancy_id": vacancy_id, "messages": []}))
            return
        msgs = self.adapter.fetch_chat(br.page, vacancy_id, log=self._log)
        self.events.put((EV_CHAT, {"vacancy_id": vacancy_id, "messages": msgs}))
        self.events.put((EV_DONE, "chat"))

    def _cmd_apply(self, crit: Criteria) -> None:
        self._stop_apply.clear()
        if self._last_suitable:
            suitable = self._last_suitable
            self._log(f"Откликаюсь на найденные вакансии: {len(suitable)} шт.")
        else:
            self._log("Сначала ищу вакансии…")
            suitable = self._do_search(crit)

        if not suitable:
            self._log("Нет подходящих вакансий для отклика. Нажмите «Найти».")
            self.events.put((EV_DONE, "apply"))
            return

        letter = crit.cover_letter.strip()
        self._log(f"Сопроводительное письмо: "
                  f"{('«' + letter[:40] + '…»') if letter else 'ПУСТО (не задано)'}")

        count = self.adapter.run_applications(
            self._ensure_browser().page,
            suitable,
            crit,
            self._storage,
            log=self._log,
            should_stop=self._stop_apply.is_set,
            on_update=lambda v: self.events.put((EV_VACANCY, v)),
        )
        self._log(f"Готово. Отправлено откликов: {count}")
        self._last_suitable = None  # список использован
        self.events.put((EV_DONE, "apply"))
