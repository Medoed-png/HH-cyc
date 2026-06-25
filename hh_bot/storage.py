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
                applied_at TEXT
            )
            """
        )
        self.conn.commit()

    def is_applied(self, vacancy_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM applied WHERE vacancy_id = ?", (vacancy_id,)
        )
        return cur.fetchone() is not None

    def mark_applied(self, vacancy_id: str, title: str, company: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO applied VALUES (?, ?, ?, ?)",
            (vacancy_id, title, company, datetime.datetime.now().isoformat()),
        )
        self.conn.commit()

    def applied_today(self) -> int:
        """Сколько откликов отправлено сегодня (для дневного лимита)."""
        today = datetime.date.today().isoformat()
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM applied WHERE applied_at LIKE ?", (today + "%",)
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self.conn.close()
