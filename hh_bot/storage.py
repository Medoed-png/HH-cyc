"""Хранилище истории откликов в SQLite (дедупликация)."""
from __future__ import annotations

import os
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "history.db")


class Storage:
    """Простое хранилище: какие вакансии видели и на какие откликнулись."""

    def __init__(self, path: str = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applied (
                vacancy_id TEXT PRIMARY KEY,
                title      TEXT,
                company    TEXT,
                applied_at TEXT,
                source     TEXT DEFAULT 'bot'
            )
            """
        )
        # Миграция для старых БД: добавить колонку source. Существующие записи
        # помечаем 'sync' — это ручные/синхронизированные отклики, они НЕ должны
        # съедать дневной лимит бота.
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(applied)").fetchall()]
        if "source" not in cols:
            self.conn.execute("ALTER TABLE applied ADD COLUMN source TEXT DEFAULT 'bot'")
            self.conn.execute("UPDATE applied SET source='sync'")
        self.conn.commit()

    def is_applied(self, vacancy_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM applied WHERE vacancy_id = ?", (vacancy_id,)
        )
        return cur.fetchone() is not None

    def mark_applied(self, vacancy_id: str, title: str, company: str,
                     source: str = "bot") -> None:
        """source='bot' — отклик бота (считается в дневной лимит);
        source='sync' — ручной/синхронизированный (только дедупликация)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO applied VALUES (?, ?, ?, ?, ?)",
            (vacancy_id, title, company, datetime.datetime.now().isoformat(), source),
        )
        self.conn.commit()

    def applied_today(self) -> int:
        """Сколько откликов БОТ отправил сегодня (для дневного лимита)."""
        today = datetime.date.today().isoformat()
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM applied WHERE applied_at LIKE ? AND source='bot'",
            (today + "%",),
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self.conn.close()
