"""Хранилище «виденных монитором» вакансий — для детекта НОВЫХ по фильтрам.

Монитор периодически ищет вакансии по критериям пользователя (БЕЗ входа — выдача
публичная) и уведомляет только о тех, что появились впервые. Здесь — тонкий слой
над таблицей monitor_seen.
"""
from __future__ import annotations

import datetime

from sqlalchemy import select

from .db import SessionLocal, MonitorSeen


def has_any(user_id: int, site_id: str) -> bool:
    """Была ли уже хоть одна виденная вакансия (для «первого запуска» = базы)."""
    with SessionLocal() as s:
        row = s.execute(
            select(MonitorSeen.id).where(
                MonitorSeen.user_id == user_id, MonitorSeen.site_id == site_id
            ).limit(1)
        ).first()
    return row is not None


def mark_and_get_new(user_id: int, site_id: str, vacancy_ids: list[str]) -> set[str]:
    """Отметить вакансии как виденные; вернуть множество НОВЫХ (ранее не встречавшихся)."""
    ids = [str(v) for v in vacancy_ids if v]
    if not ids:
        return set()
    now = datetime.datetime.now()
    with SessionLocal() as s:
        existing = {
            vid for (vid,) in s.execute(
                select(MonitorSeen.vacancy_id).where(
                    MonitorSeen.user_id == user_id, MonitorSeen.site_id == site_id,
                    MonitorSeen.vacancy_id.in_(ids),
                )
            ).all()
        }
        new = set()
        for vid in ids:
            if vid not in existing and vid not in new:
                s.add(MonitorSeen(user_id=user_id, site_id=site_id,
                                  vacancy_id=vid, first_seen_at=now))
                new.add(vid)
        if new:
            s.commit()
    return new
