"""Поле ввода с автоподсказками (выпадающий список).

Поддерживает список значений через запятую: подсказки строятся по последнему
введённому слову (после последней запятой). Выбор подставляет профессию и
добавляет ", " для ввода следующей.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk


class AutocompleteEntry(ttk.Entry):
    def __init__(self, master, suggestions, textvariable=None, max_items=10,
                 multi=True, source=None, **kw):
        self.var = textvariable or tk.StringVar()
        super().__init__(master, textvariable=self.var, **kw)
        self.suggestions = list(suggestions)
        self.max_items = max_items
        # multi=True — список через запятую (профессии);
        # multi=False — одно значение (город).
        self.multi = multi
        # source(token) -> list[str] — «живой» источник подсказок (напр. hh.ru).
        # Если задан, запрашивается асинхронно с откатом на статический список.
        self.source = source
        self._popup: tk.Toplevel | None = None
        self._listbox: tk.Listbox | None = None
        self._debounce_id = None      # id отложенного запроса
        self._req_seq = 0             # счётчик запросов (отсев устаревших ответов)

        self.bind("<KeyRelease>", self._on_key)
        self.bind("<Down>", self._focus_list)
        self.bind("<Escape>", lambda e: self._hide())

    # --- текущее слово (для multi — после последней запятой) ---
    def _current_token(self) -> str:
        text = self.var.get()
        if not self.multi:
            return text.strip()
        idx = text.rfind(",")
        return text[idx + 1:].strip()

    def _on_key(self, event) -> None:
        # Навигационные клавиши не триггерят пересборку.
        if event.keysym in ("Up", "Down", "Return", "Escape", "Left", "Right"):
            return
        token = self._current_token()
        if len(token) < 1:
            self._hide()
            return
        if self.source is not None:
            self._query_async(token)   # «живой» источник (hh.ru)
        else:
            self._show_local(token)    # статический список

    def _show_local(self, token: str) -> None:
        """Подсказки из статического списка self.suggestions."""
        tl = token.lower()
        starts = sorted(s for s in self.suggestions if s.lower().startswith(tl))
        contains = sorted(
            s for s in self.suggestions if tl in s.lower() and not s.lower().startswith(tl)
        )
        matches = (starts + contains)[: self.max_items]
        if not matches:
            self._hide()
            return
        self._show(matches)

    def _query_async(self, token: str) -> None:
        """Запросить подсказки у источника асинхронно (с антидребезгом)."""
        if self._debounce_id is not None:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(220, lambda t=token: self._start_query(t))

    def _start_query(self, token: str) -> None:
        self._req_seq += 1
        seq = self._req_seq
        threading.Thread(target=self._worker, args=(token, seq), daemon=True).start()

    def _worker(self, token: str, seq: int) -> None:
        try:
            results = self.source(token) or []
        except Exception:
            results = []
        try:
            self.after(0, lambda: self._deliver(token, seq, results))
        except RuntimeError:
            pass  # окно уже закрыто

    def _deliver(self, token: str, seq: int, results: list) -> None:
        # Игнорируем устаревший ответ или если пользователь уже печатает другое.
        if seq != self._req_seq or self._current_token() != token:
            return
        if not results:
            self._hide()
            return
        self._show(results[: self.max_items])

    def _show(self, matches: list[str]) -> None:
        if self._popup is None:
            self._popup = tk.Toplevel(self)
            self._popup.wm_overrideredirect(True)
            self._popup.attributes("-topmost", True)
            self._listbox = tk.Listbox(self._popup, activestyle="dotbox")
            self._listbox.pack(fill="both", expand=True)
            self._listbox.bind("<ButtonRelease-1>", self._choose)
            self._listbox.bind("<Return>", self._choose)
            self._listbox.bind("<Escape>", lambda e: self._hide())
        assert self._listbox is not None
        self._listbox.delete(0, "end")
        for m in matches:
            self._listbox.insert("end", m)
        self._listbox.configure(height=min(len(matches), self.max_items))
        # Позиционируем popup прямо под полем ввода.
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        self._popup.wm_geometry(f"{self.winfo_width()}x{self._listbox.winfo_reqheight()}+{x}+{y}")
        self._popup.deiconify()
        self._popup.lift()

    def _focus_list(self, event=None) -> None:
        if self._listbox is not None and self._popup is not None and self._popup.winfo_viewable():
            self._listbox.focus_set()
            self._listbox.selection_clear(0, "end")
            self._listbox.selection_set(0)
            self._listbox.activate(0)

    def _choose(self, event=None) -> None:
        if self._listbox is None:
            return
        sel = self._listbox.curselection()
        if not sel:
            return
        choice = self._listbox.get(sel[0])
        if not self.multi:
            # Одно значение — просто подставляем выбранное.
            self.var.set(choice)
        else:
            text = self.var.get()
            idx = text.rfind(",")
            prefix = text[: idx + 1].rstrip() if idx >= 0 else ""
            self.var.set((prefix + " " + choice if prefix else choice) + ", ")
        self._hide()
        self.focus_set()
        self.icursor("end")

    def _hide(self, event=None) -> None:
        if self._popup is not None:
            self._popup.withdraw()
