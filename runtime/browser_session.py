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
EV_LETTER = "letter"        # payload: dict {vacancy_id, title, company, url, text}
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
        # Идёт интерактивный вход (ждём код из SMS/капчу): пока взведён — не
        # навигируем страницу авто-проверкой входа, иначе страница кода теряется.
        self._interactive_login = False
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
        prefix = f"[bot u{self.user_id}/{self.site_id}]"
        try:
            print(prefix, msg, flush=True)
        except Exception:  # noqa: BLE001 — консоль cp1251 не кодирует юникод (✓, эмодзи)
            try:
                print(prefix, msg.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:  # noqa: BLE001
                pass
        # В UI (через SSE) уходит исходный текст с юникодом — там кодировка UTF-8.
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
            self._interactive_login = False
            credentials.set_status(self.user_id, self.site_id,
                                   credentials.STATUS_CONNECTED, logged_in=True)
            self._persist_cookies()
            self._go_background()  # автологин шёл в фоне; окна и не было, но на всякий
            self.events.put((EV_LOGIN, True))
        elif st == LoginStatus.SMS_REQUIRED:
            # Страница оставлена на шаге кода; ждём команду submit_sms (не блокируем поток).
            # Взводим флаг: авто-check_login не должен навигировать со страницы кода.
            self._interactive_login = True
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_NEEDS_SMS)
        elif st == LoginStatus.CAPTCHA_REQUIRED:
            self._interactive_login = True
            credentials.set_status(self.user_id, self.site_id, credentials.STATUS_NEEDS_CAPTCHA)
            # Капчу-картинку нельзя пройти автоматически. НЕ открываем окно сразу —
            # шлём статус needs_captcha: дашборд покажет заметное окно с кнопкой
            # «Пройти капчу», и уже по ней пользователь откроет видимый браузер.
            self._log("Сайт показал капчу — нажмите «Пройти капчу» во всплывающем окне, "
                      "чтобы открыть браузер и завершить вход.")
        else:  # BAD_CREDENTIALS | FAILED
            self._interactive_login = False
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
        # Синхронизируем статус: сессия больше не авторизована. Без этого в БД
        # оставался бы status='connected', и UI устойчиво показывал бы «подключён».
        self._interactive_login = False
        credentials.set_status(self.user_id, self.site_id, credentials.STATUS_INVALID)
        self._log(f"Вышли из аккаунта {self.adapter.display_name} — можно подключить другой.")
        self.events.put((EV_LOGIN, False))
        self._emit_conn()  # conn_status='invalid' -> панель вернётся к форме входа
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
            self._log(f"Авто-вход для {self.adapter.display_name} пока не реализован — "
                      f"войдите вручную: нажмите «Войти вручную в окне».")
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
        # Во время интерактивного входа (ждём код/капчу) НЕ навигируем страницу:
        # is_logged_in ушёл бы со страницы кода и сломал бы последующий submit_sms.
        if self._interactive_login:
            self.events.put((EV_DONE, "check_login"))
            return
        br = self._ensure_browser()
        logged = self.adapter.is_logged_in(br.page)
        if logged:
            self._persist_cookies()  # закрепить вход на диске сразу
            self._go_background()    # убрать видимое окно входа в фон
            # Зафиксировать статус 'connected' (если есть строка кред) — иначе после
            # перезагрузки страницы карточка кратко показывала бы «не подключён».
            credentials.set_status(self.user_id, self.site_id,
                                   credentials.STATUS_CONNECTED, logged_in=True)
        self._log("Авторизация подтверждена." if logged else "Вы ещё не вошли.")
        self.events.put((EV_LOGIN, logged))
        self._emit_conn()
        self.events.put((EV_DONE, "check_login"))

    def _do_search(self, crit: Criteria) -> list:
        """Найти и отфильтровать вакансии, вывести подходящие в таблицу.

        Поиск ПУБЛИЧНЫЙ — вход не требуется (выдача сайтов открыта). Вход нужен
        только для авто-отклика/ответов (см. _cmd_apply).
        """
        br = self._ensure_browser()
        all_found = []
        # «Все страницы» — высокий потолок (поиск сам остановится, когда вакансии
        # закончатся); иначе ограничиваемся max_pages.
        if getattr(crit, "all_pages", False):
            pages = 50  # практический предел выдачи hh.ru (~40 стр. × 50)
            self._log("Режим «все страницы»: сканирую всю выдачу по фильтру — "
                      "это может занять заметное время…")
        else:
            pages = crit.max_pages
        # Без профессии — один поиск с пустым запросом: сайт вернёт ВСЕ вакансии
        # по остальным фильтрам (регион, опыт, занятость, график/удалёнка).
        queries = crit.profession_texts or [""]
        for text in queries:
            if self._stop_apply.is_set():  # «Стоп» прерывает между профессиями
                self._log("Поиск остановлен.")
                break
            antiban.rate_limit(self.user_id)  # разнести запросы во времени
            self._log(f"Поиск: {text or 'все вакансии (без профессии)'}")
            found = self.adapter.search(
                br.page, text, crit.region, pages, log=self._log,
                experience=crit.experience, employment=crit.employment,
                schedule=crit.schedule, should_stop=self._stop_apply.is_set,
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
        self._stop_apply.clear()  # сброс возможного стопа от прошлой операции
        self._do_search(crit)
        self.events.put((EV_DONE, "search"))

    def _cmd_monitor(self, crit: Criteria) -> None:
        """Мониторинг новых вакансий по фильтрам БЕЗ входа: поиск + детект новых.

        Первый запуск берёт текущую выдачу за базу (без уведомлений). Далее шлёт
        Telegram-уведомление только о вакансиях, появившихся впервые. Отклик НЕ
        отправляем — только показываем/уведомляем.
        """
        from hh_bot import monitor
        self._stop_apply.clear()
        suitable = self._do_search(crit)  # публичный поиск + вывод в таблицу
        ids = [v.vacancy_id for v in suitable]
        first_run = not monitor.has_any(self.user_id, self.site_id)
        new_ids = monitor.mark_and_get_new(self.user_id, self.site_id, ids)
        new_vacs = [v for v in suitable if v.vacancy_id in new_ids]
        if first_run:
            self._log(f"Мониторинг запущен: {len(suitable)} вакансий взяты за базу "
                      f"(уведомлений о них не будет).")
        elif new_vacs:
            self._log(f"🔔 Новых вакансий по фильтрам: {len(new_vacs)}")
            self._notify_telegram_new(new_vacs)
        else:
            self._log("Мониторинг: новых вакансий нет.")
        self.events.put((EV_DONE, "monitor"))

    def _notify_telegram_new(self, vacs: list) -> None:
        """Telegram-уведомление о новых вакансиях монитора (если задан chat_id)."""
        chat_id = credentials.get_telegram(self.user_id)
        if not chat_id:
            return
        lines = [f"🔎 Новые вакансии — {self.adapter.display_name}:"]
        for v in vacs[:15]:
            sal = f" · {v.salary}" if getattr(v, "salary", "") else ""
            lines.append(f"• {v.title} — {v.company or ''}{sal}\n{v.url}")
        if notify.send_telegram(chat_id, "\n".join(lines)):
            self._log(f"Telegram: уведомление о {len(vacs)} новых вакансиях отправлено.")

    def _cmd_responses(self) -> None:
        """Собрать ответы работодателей и отправить их в интерфейс."""
        br = self._ensure_browser()
        if not self.adapter.is_logged_in(br.page):
            self._log(f"Сессия {self.adapter.display_name} не активна — войдите заново.")
            # Доносим до UI, что не вошли: иначе «Загружаю…» висел бы вечно.
            self.events.put((EV_RESPONSES, {"items": [], "unread": 0, "logged_out": True}))
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

    def _cmd_letter(self, vacancy: dict) -> None:
        """Сгенерировать сопроводительное письмо для вакансии (preview, БЕЗ входа).

        Открываем публичную страницу вакансии, читаем описание и строим письмо
        через hh_bot.letter (без ИИ). Отклик НЕ отправляем — только показываем текст.
        """
        from hh_bot import letter as letter_mod
        from hh_bot import config as config_mod
        from hh_bot.models import Vacancy
        v = Vacancy(
            vacancy_id=str(vacancy.get("id", "")),
            title=vacancy.get("title", ""), company=vacancy.get("company", ""),
            url=vacancy.get("url", ""), profession=vacancy.get("profession", ""),
            site=self.site_id,
        )
        if not v.url:
            self.events.put((EV_DONE, "letter"))
            return
        br = self._ensure_browser()  # публичная страница — вход не требуется
        self._log(f"Генерирую письмо для «{v.title}»…")
        crit = config_mod.load_for(self.user_id, self.site_id)
        desc = self.adapter.fetch_description(br.page, v.url)
        text = letter_mod.build_letter(v, desc, crit)
        self.events.put((EV_LETTER, {
            "vacancy_id": v.vacancy_id, "title": v.title, "company": v.company,
            "url": v.url, "text": text,
        }))
        self.events.put((EV_DONE, "letter"))

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
            # Перефильтровываем кеш под ТЕКУЩИЕ критерии: иначе изменённые после
            # «Найти» фильтры (исключения/зарплата/чёрный список/строгий отбор)
            # игнорировались бы, и бот делал необратимые отклики на лишние вакансии.
            suitable = filters.filter_all(self._last_suitable, crit, self._storage)
            self._log(f"Откликаюсь на найденные вакансии: {len(suitable)} шт. "
                      f"(после фильтра по текущим критериям).")
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
