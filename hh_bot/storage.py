"""Хранилище истории откликов (дедупликация + дневной лимит).

Репозиторий, привязанный к (user_id, site_id): каждый пользователь и каждый сайт
видят только свои отклики. Сигнатуры методов (is_applied/mark_applied/applied_today)
сохранены — фильтрация и цикл откликов их не замечают. Бэкенд — SQLAlchemy
(SQLite локально, Postgres в облаке), см. hh_bot/db.py.
"""
from __future__ import annotations

import datetime

from sqlalchemy import select, func

from .db import SessionLocal, AppliedHistory, init_db

# Схема + перенос старой history.db создаются один раз на процесс.
_initialized = False


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


class Storage:
    """История откликов в рамках одного (user_id, site_id)."""

    def __init__(self, user_id: int = 0, site_id: str = "hh"):
        _ensure_init()
        self.user_id = user_id
        self.site_id = site_id

    def is_applied(self, vacancy_id: str) -> bool:
        with SessionLocal() as s:
            stmt = select(AppliedHistory.id).where(
                AppliedHistory.user_id == self.user_id,
                AppliedHistory.site_id == self.site_id,
                AppliedHistory.vacancy_id == str(vacancy_id),
            )
            return s.execute(stmt).first() is not None

    def mark_applied(self, vacancy_id: str, title: str, company: str,
                     source: str = "bot") -> None:
        """source='bot' — отклик бота (считается в дневной лимит);
        source='sync' — ручной/синхронизированный (только дедупликация)."""
        vacancy_id = str(vacancy_id)
        with SessionLocal() as s:
            row = s.execute(
                select(AppliedHistory).where(
                    AppliedHistory.user_id == self.user_id,
                    AppliedHistory.site_id == self.site_id,
                    AppliedHistory.vacancy_id == vacancy_id,
                )
            ).scalar_one_or_none()
            now = datetime.datetime.now()
            if row is None:
                s.add(AppliedHistory(
                    user_id=self.user_id, site_id=self.site_id, vacancy_id=vacancy_id,
                    title=title or "", company=company or "", applied_at=now,
                    source=source,
                ))
            else:
                row.title = title or row.title
                row.company = company or row.company
                row.applied_at = now
                row.source = source
            s.commit()

    def applied_today(self) -> int:
        """Сколько откликов БОТ отправил сегодня (для дневного лимита)."""
        start = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
        end = start + datetime.timedelta(days=1)
        with SessionLocal() as s:
            stmt = select(func.count()).select_from(AppliedHistory).where(
                AppliedHistory.user_id == self.user_id,
                AppliedHistory.site_id == self.site_id,
                AppliedHistory.source == "bot",
                AppliedHistory.applied_at >= start,
                AppliedHistory.applied_at < end,
            )
            return int(s.execute(stmt).scalar_one())

    def set_status(self, vacancy_id: str, status: str) -> str | None:
        """Записать последний статус отклика (от работодателя). Возвращает прежний
        статус, если он изменился, иначе None (для детекта новых событий)."""
        vacancy_id = str(vacancy_id)
        new = (status or "").strip()
        with SessionLocal() as s:
            row = s.execute(
                select(AppliedHistory).where(
                    AppliedHistory.user_id == self.user_id,
                    AppliedHistory.site_id == self.site_id,
                    AppliedHistory.vacancy_id == vacancy_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            prev = row.last_status or ""
            if new and new != prev:
                row.last_status = new
                s.commit()
                return prev
            return None

    def stats(self) -> dict:
        """Агрегаты для дашборда: отправлено, приглашения, отказы, просмотры, конверсия."""
        with SessionLocal() as s:
            def cnt(*conds) -> int:
                stmt = select(func.count()).select_from(AppliedHistory).where(
                    AppliedHistory.user_id == self.user_id,
                    AppliedHistory.site_id == self.site_id, *conds)
                return int(s.execute(stmt).scalar_one())

            total = cnt()
            sent_bot = cnt(AppliedHistory.source == "bot")
            statuses = s.execute(
                select(AppliedHistory.last_status).where(
                    AppliedHistory.user_id == self.user_id,
                    AppliedHistory.site_id == self.site_id)
            ).all()

        invitations = rejections = viewed = 0
        for (st,) in statuses:
            low = (st or "").lower()
            if any(k in low for k in ("приглаш", "собеседов", "оффер")):
                invitations += 1
            elif "отказ" in low:
                rejections += 1
            elif "просмотр" in low:
                viewed += 1
        conversion = round(invitations / total * 100, 1) if total else 0.0
        return {
            "applied_total": total,
            "applied_bot": sent_bot,
            "applied_today": self.applied_today(),
            "invitations": invitations,
            "rejections": rejections,
            "viewed": viewed,
            "conversion": conversion,
        }

    def close(self) -> None:
        """Совместимость: сессии короткоживущие, закрывать нечего."""
        return
