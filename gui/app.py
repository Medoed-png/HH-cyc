"""Графический интерфейс (Tkinter) для HH-бота."""
from __future__ import annotations

import queue
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

from hh_bot import config as config_mod
from hh_bot.config import Criteria
from hh_bot.professions_list import PROFESSIONS
from hh_bot.cities_list import CITIES
from hh_bot.suggest import fetch_suggestions
from hh_bot.worker import Worker, EV_LOG, EV_LOGIN, EV_VACANCY
from gui.autocomplete import AutocompleteEntry

# Обратный справочник: id региона -> название (для отображения в поле).
_ID_TO_CITY = {v: k for k, v in CITIES.items()}

# Колонки таблицы вакансий: (ключ, заголовок, ширина).
_TABLE_COLUMNS = [
    ("title", "Вакансия", 180),
    ("company", "Компания", 180),
    ("salary", "Зарплата", 120),
    ("status", "Статус", 120),
    ("note", "Примечание", 120),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HH-бот — авто-отклик на вакансии")
        self.geometry("1000x720")

        self.crit = config_mod.load()
        self.worker = Worker()
        self.worker.start()

        self._vac_rows: dict[str, str] = {}  # vacancy_id -> tree item id
        self._vac_urls: dict[str, str] = {}  # tree item id -> url вакансии

        self._build_ui()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Проверим статус логина при старте.
        self.worker.submit("check_login")

    # ================= построение UI =================
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        self._build_form(root)
        self._build_buttons(root)
        self._build_table(root)
        self._build_log(root)

    def _build_form(self, root: ttk.Frame) -> None:
        """Панель критериев с полями ввода."""
        form = ttk.LabelFrame(root, text="Критерии", padding=8)
        form.pack(fill="x")

        self.v_prof = tk.StringVar(value=", ".join(self.crit.profession_texts))
        # В поле региона показываем НАЗВАНИЕ города (id берём из справочника).
        self.v_region = tk.StringVar(value=_ID_TO_CITY.get(str(self.crit.region), "Россия"))
        self.v_salary = tk.StringVar(value=str(self.crit.salary_min))
        self.v_exclude = tk.StringVar(value=", ".join(self.crit.exclude_words))
        self.v_include = tk.StringVar(value=", ".join(self.crit.include_words))
        self.v_resume = tk.StringVar(value=self.crit.resume_name)
        self.v_limit = tk.StringVar(value=str(self.crit.daily_limit))
        self.v_pages = tk.StringVar(value=str(self.crit.max_pages))

        rows = [
            ("Профессии (через запятую):", self.v_prof),
            ("Город / регион:", self.v_region),
            ("Мин. зарплата:", self.v_salary),
            ("Исключающие слова:", self.v_exclude),
            ("Обязательные слова:", self.v_include),
            ("Имя резюме:", self.v_resume),
            ("Дневной лимит откликов:", self.v_limit),
            ("Макс. страниц на запрос:", self.v_pages),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            self._make_field(form, var).grid(row=i, column=1, sticky="we", padx=4, pady=2)
        form.columnconfigure(1, weight=1)
        # Отформатировать стартовое значение зарплаты из конфига.
        self.v_salary.set(self._group_digits(self.v_salary.get()))

        # Сопроводительное письмо — отдельным многострочным полем.
        letter_row = len(rows)
        ttk.Label(form, text="Сопроводительное письмо:").grid(
            row=letter_row, column=0, sticky="nw", padx=4, pady=2
        )
        self.t_letter = tk.Text(form, height=4, width=70, wrap="word")
        self.t_letter.insert("1.0", self.crit.cover_letter)
        self.t_letter.grid(row=letter_row, column=1, sticky="we", padx=4, pady=2)

    def _make_field(self, form: ttk.Frame, var: tk.StringVar) -> ttk.Entry:
        """Создать поле ввода нужного типа в зависимости от переменной."""
        if var is self.v_prof:
            # Живые подсказки hh.ru (несколько профессий через запятую).
            return AutocompleteEntry(form, PROFESSIONS, textvariable=var, width=70,
                                     multi=True, source=self._profession_source)
        if var is self.v_region:
            # Город из справочника (одно значение).
            return AutocompleteEntry(form, sorted(CITIES.keys()), textvariable=var,
                                     width=70, multi=False)
        entry = ttk.Entry(form, textvariable=var, width=70)
        if var is self.v_salary:
            # Группируем цифры точками прямо во время ввода.
            entry.bind("<KeyRelease>", self._format_salary)
        return entry

    def _build_buttons(self, root: ttk.Frame) -> None:
        """Панель статуса и кнопок управления."""
        bar = ttk.Frame(root, padding=(0, 8))
        bar.pack(fill="x")
        self.lbl_status = ttk.Label(bar, text="Статус: проверяю вход…")
        self.lbl_status.pack(side="left")

        # Кнопки добавляются справа налево.
        buttons = [
            ("Сохранить критерии", self._save),
            ("Войти", self._login),
            ("Открыть вакансию", self._open_selected),
            ("Найти", self._search),
            ("Запустить отклики", self._apply),
            ("Стоп", self._stop),
        ]
        for text, command in buttons:
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=3)

    def _build_table(self, root: ttk.Frame) -> None:
        """Таблица найденных вакансий."""
        frame = ttk.LabelFrame(root, text="Вакансии", padding=4)
        frame.pack(fill="both", expand=True)
        columns = [c[0] for c in _TABLE_COLUMNS]
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        for key, header, width in _TABLE_COLUMNS:
            self.tree.heading(key, text=header)
            self.tree.column(key, width=width)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        # Двойной клик по строке открывает вакансию в браузере.
        self.tree.bind("<Double-1>", lambda e: self._open_selected())

    def _build_log(self, root: ttk.Frame) -> None:
        """Панель лога."""
        frame = ttk.LabelFrame(root, text="Лог", padding=4)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, height=8, state="disabled", wrap="word")
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

    # ================= подсказки профессий =================
    @staticmethod
    def _profession_source(token: str) -> list:
        """Живые подсказки hh.ru; при отсутствии сети — встроенный список."""
        items = fetch_suggestions(token)
        if items:
            return items
        # Офлайн-откат: фильтрация встроенного справочника.
        tl = token.lower()
        starts = sorted(p for p in PROFESSIONS if p.lower().startswith(tl))
        contains = sorted(
            p for p in PROFESSIONS if tl in p.lower() and not p.lower().startswith(tl)
        )
        return starts + contains

    # ================= форматирование зарплаты =================
    @staticmethod
    def _group_digits(value: str) -> str:
        """'200000' -> '200.000'. Нецифры игнорируются."""
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return ""
        return f"{int(digits):,}".replace(",", ".")

    def _format_salary(self, event=None) -> None:
        """Переформатировать поле зарплаты при вводе, сохранив позицию курсора."""
        self.v_salary.set(self._group_digits(self.v_salary.get()))
        if event is not None:
            event.widget.icursor("end")  # курсор в конец после переформатирования

    # ================= сбор критериев из формы =================
    @staticmethod
    def _split(text: str) -> list:
        """Строку через запятую -> список непустых значений."""
        return [x.strip() for x in text.split(",") if x.strip()]

    @staticmethod
    def _to_int(text: str, default: int) -> int:
        try:
            return int(text.strip())
        except ValueError:
            return default

    def _city_id(self) -> int:
        """Название города из поля -> id региона hh.ru (113 = вся Россия)."""
        name = self.v_region.get().strip()
        city_id = CITIES.get(name)
        if city_id is None:
            low = {k.lower(): v for k, v in CITIES.items()}
            city_id = low.get(name.lower(), "113")
        return int(city_id)

    def _collect(self) -> Criteria:
        """Считать значения формы в объект критериев."""
        self.crit.professions = [{"text": t} for t in self._split(self.v_prof.get())]
        self.crit.region = self._city_id()
        # Зарплата может содержать точки-разделители — оставляем только цифры.
        salary_digits = "".join(ch for ch in self.v_salary.get() if ch.isdigit())
        self.crit.salary_min = int(salary_digits) if salary_digits else 0
        self.crit.exclude_words = self._split(self.v_exclude.get())
        self.crit.include_words = self._split(self.v_include.get())
        self.crit.resume_name = self.v_resume.get().strip()
        self.crit.daily_limit = self._to_int(self.v_limit.get(), 150)
        self.crit.max_pages = self._to_int(self.v_pages.get(), 5)
        self.crit.cover_letter = self.t_letter.get("1.0", "end").strip()
        return self.crit

    # ================= действия кнопок =================
    def _save(self) -> None:
        config_mod.save(self._collect())
        self._append_log("Критерии сохранены в config.yaml.")

    def _login(self) -> None:
        self.worker.submit("login")

    def _search(self) -> None:
        self._clear_table()
        self.worker.submit("search", crit=self._collect())

    def _apply(self) -> None:
        crit = self._collect()
        if not messagebox.askyesno(
            "Подтверждение",
            f"Запустить АВТО-отклики?\nДневной лимит: {crit.daily_limit}.\n"
            "Бот будет откликаться сам на все подходящие вакансии.",
        ):
            return
        self._clear_table()
        self.worker.submit("apply", crit=crit)

    def _stop(self) -> None:
        self.worker.request_stop_apply()
        self._append_log("Запрошена остановка…")

    # ================= таблица и лог =================
    def _clear_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._vac_rows.clear()
        self._vac_urls.clear()

    def _upsert_vacancy(self, vacancy) -> None:
        """Добавить или обновить строку вакансии в таблице."""
        row = vacancy.as_row()
        item = self._vac_rows.get(vacancy.vacancy_id)
        if item is not None:
            self.tree.item(item, values=row)
        else:
            item = self.tree.insert("", "end", values=row)
            self._vac_rows[vacancy.vacancy_id] = item
        self._vac_urls[item] = vacancy.url

    def _open_selected(self) -> None:
        """Открыть выбранную вакансию в браузере по умолчанию."""
        selection = self.tree.selection()
        if not selection:
            self._append_log("Выберите вакансию в таблице, чтобы открыть её.")
            return
        url = self._vac_urls.get(selection[0])
        if url:
            webbrowser.open(url)
            self._append_log(f"Открываю в браузере: {url}")
        else:
            self._append_log("У выбранной строки нет ссылки.")

    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ================= опрос событий от воркера =================
    def _handle_event(self, kind: str, payload) -> None:
        if kind == EV_LOG:
            self._append_log(payload)
        elif kind == EV_LOGIN:
            self.lbl_status.configure(
                text="Статус: вы вошли ✓" if payload else "Статус: не авторизованы"
            )
        elif kind == EV_VACANCY:
            self._upsert_vacancy(payload)
        # EV_DONE — пока без действий.

    def _poll_events(self) -> None:
        """Периодически разбирать очередь событий от рабочего потока."""
        try:
            while True:
                kind, payload = self.worker.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_events)

    def _on_close(self) -> None:
        self.worker.request_stop_apply()
        self.worker.shutdown()
        self.destroy()


def main() -> None:
    App().mainloop()
