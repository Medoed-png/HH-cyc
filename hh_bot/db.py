"""Слой базы данных приложения (SQLAlchemy 2.x, DB-agnostic).

Локально — SQLite-файл app.db в корне проекта; в облаке (M8) переключается на
Postgres через переменную окружения DATABASE_URL без изменения кода. Схема уже
рассчитана на мультипользовательский/мультисайт режим: записи привязаны к
(user_id, site_id). Пока user_id=0 (один пользователь) — таблица users и реальные
пользователи появятся в M3.

Схема создаётся через create_all (Alembic-миграции подключим, когда схема
стабилизируется / при переезде на Postgres).
"""
from __future__ import annotations

import os
import datetime

from sqlalchemy import (
    JSON, String, Integer, DateTime, UniqueConstraint, Index, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# URL БД: по умолчанию локальный SQLite-файл в корне проекта.
_DEFAULT_SQLITE = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "app.db"
).replace("\\", "/")
DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_SQLITE)

# Для SQLite нужен check_same_thread=False: с БД работает поток воркера.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь сервиса (вход в само приложение, не в hh.ru)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.now
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Прокси пользователя для анти-бана (зашифрован Fernet). Пусто = без прокси.
    proxy_url_enc: Mapped[str] = mapped_column(String(512), default="")
    # Telegram chat_id для уведомлений (не секрет, plain). Пусто = выключено.
    telegram_chat_id: Mapped[str] = mapped_column(String(64), default="")


class SiteConfig(Base):
    """Критерии поиска одного пользователя для одного сайта (замена config.yaml)."""

    __tablename__ = "site_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    site_id: Mapped[str] = mapped_column(String(32), default="hh")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.now
    )

    __table_args__ = (
        UniqueConstraint("user_id", "site_id", name="uq_siteconfig_user_site"),
    )


class SiteCredential(Base):
    """Учётные данные пользователя для входа на сайт (логин/пароль зашифрованы).

    Пароль и логин хранятся в виде Fernet-токенов (см. hh_bot/crypto.py) — их
    можно расшифровать только мастер-ключом сервиса. status отражает состояние
    подключения аккаунта: connected / needs_sms / needs_captcha / invalid.
    """

    __tablename__ = "site_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    site_id: Mapped[str] = mapped_column(String(32), default="hh")
    username_enc: Mapped[str] = mapped_column(String(512), default="")
    password_enc: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="invalid")
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.now
    )

    __table_args__ = (
        UniqueConstraint("user_id", "site_id", name="uq_sitecred_user_site"),
    )


class AppliedHistory(Base):
    """История откликов с привязкой к пользователю и сайту (дедуп + дневной лимит)."""

    __tablename__ = "applied_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    site_id: Mapped[str] = mapped_column(String(32), default="hh")
    vacancy_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512), default="")
    company: Mapped[str] = mapped_column(String(512), default="")
    applied_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.now
    )
    # 'bot' — отклик бота (считается в дневной лимит); 'sync' — ручной/синхронизированный.
    source: Mapped[str] = mapped_column(String(16), default="bot")
    # Последний известный статус отклика со стороны работодателя (для аналитики):
    # '', 'приглашение', 'отказ', 'просмотрен', 'сообщение' и т.п.
    last_status: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (
        UniqueConstraint("user_id", "site_id", "vacancy_id",
                         name="uq_applied_user_site_vacancy"),
        Index("ix_applied_user_site_date", "user_id", "site_id", "applied_at"),
    )


def init_db() -> None:
    """Создать таблицы (идемпотентно) и перенести старую историю откликов.

    Для локальной разработки достаточно create_all; в проде схемой управляет
    Alembic (`alembic upgrade head`, см. migrations/). _ensure_columns добавляет
    колонки, появившиеся позже, в уже существующую локальную БД (create_all их
    не доводит до существующих таблиц).
    """
    Base.metadata.create_all(engine)
    _ensure_columns()
    migrate_legacy_history()


def _ensure_columns() -> None:
    """Идемпотентно добавить недостающие колонки в существующие таблицы (dev).

    SQLite/Postgres понимают ALTER TABLE ... ADD COLUMN. Для свежей БД колонки уже
    созданы create_all, поэтому добавляем только отсутствующие.
    """
    from sqlalchemy import inspect, text

    wanted = {
        "users": [("proxy_url_enc", "VARCHAR(512) DEFAULT ''"),
                  ("telegram_chat_id", "VARCHAR(64) DEFAULT ''")],
        "applied_history": [("last_status", "VARCHAR(64) DEFAULT ''")],
    }
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))


def migrate_legacy_history() -> None:
    """Идемпотентно перенести строки из старой history.db (таблица applied).

    Старые записи получают user_id=0, site_id='hh'. Запускается при старте;
    повторный запуск ничего не дублирует (проверяем по уникальному ключу).
    """
    import sqlite3

    legacy_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "history.db")
    if not os.path.exists(legacy_path):
        return

    try:
        conn = sqlite3.connect(legacy_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(applied)").fetchall()]
        if not cols:
            conn.close()
            return
        has_source = "source" in cols
        rows = conn.execute(
            "SELECT vacancy_id, title, company, applied_at"
            + (", source" if has_source else "") + " FROM applied"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return

    if not rows:
        return

    with SessionLocal() as session:
        # Уже перенесённые id (user 0 / site hh), чтобы не дублировать.
        existing = {
            vid for (vid,) in session.query(AppliedHistory.vacancy_id)
            .filter(AppliedHistory.user_id == 0, AppliedHistory.site_id == "hh").all()
        }
        added = 0
        for row in rows:
            vacancy_id = str(row[0])
            if vacancy_id in existing:
                continue
            applied_at = _parse_dt(row[3])
            source = (row[4] if has_source and len(row) > 4 else "bot") or "bot"
            session.add(AppliedHistory(
                user_id=0, site_id="hh", vacancy_id=vacancy_id,
                title=row[1] or "", company=row[2] or "",
                applied_at=applied_at, source=source,
            ))
            existing.add(vacancy_id)
            added += 1
        if added:
            session.commit()
            print(f"[db] Перенесено из history.db: {added} записей", flush=True)


def _parse_dt(value) -> datetime.datetime:
    """Разобрать ISO-строку даты из старой БД; при ошибке — текущее время."""
    if isinstance(value, str) and value:
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.datetime.now()
