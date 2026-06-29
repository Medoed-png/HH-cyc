"""Репозиторий учётных данных сайтов (per-user, per-site), с шифрованием.

Логин/пароль для входа на сайт поиска работы шифруются через hh_bot.crypto
(Fernet) и хранятся в таблице site_credentials. Здесь — тонкий слой store/get
поверх неё: наружу отдаём расшифрованные значения только методом get() (его
зовёт воркер прямо перед логином); для UI есть status() без пароля.

Статусы подключения аккаунта:
  connected     — вход подтверждён, сессия активна;
  needs_sms     — сайт запросил SMS-код, ждём ввода;
  needs_captcha — нужна капча/ручное действие;
  invalid       — нет кред либо они не подошли.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import select

from .crypto import encrypt, decrypt
from .db import SessionLocal, SiteCredential

# Допустимые значения status (для валидации/документации).
STATUS_CONNECTED = "connected"
STATUS_NEEDS_SMS = "needs_sms"
STATUS_NEEDS_CAPTCHA = "needs_captcha"
STATUS_INVALID = "invalid"


@dataclass
class Creds:
    """Расшифрованные креды для входа на сайт (живут только в памяти воркера)."""

    username: str
    password: str


def store(user_id: int, site_id: str, username: str, password: str,
          status: str = STATUS_INVALID) -> None:
    """Сохранить (зашифровав) логин/пароль пользователя для сайта. Upsert по (user, site)."""
    now = datetime.datetime.now()
    with SessionLocal() as s:
        row = s.execute(
            select(SiteCredential).where(
                SiteCredential.user_id == user_id, SiteCredential.site_id == site_id
            )
        ).scalar_one_or_none()
        if row is None:
            row = SiteCredential(user_id=user_id, site_id=site_id)
            s.add(row)
        row.username_enc = encrypt(username)
        row.password_enc = encrypt(password)
        row.status = status
        row.updated_at = now
        s.commit()


def get(user_id: int, site_id: str) -> Creds | None:
    """Вернуть расшифрованные креды или None, если их нет.

    ⚠️ Возвращает пароль в открытом виде — использовать только в памяти воркера
    непосредственно перед логином, не логировать и не передавать в UI.
    """
    with SessionLocal() as s:
        row = s.execute(
            select(SiteCredential).where(
                SiteCredential.user_id == user_id, SiteCredential.site_id == site_id
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        username = decrypt(row.username_enc)
        password = decrypt(row.password_enc)
        if not username and not password:
            return None
        return Creds(username=username, password=password)


def set_status(user_id: int, site_id: str, status: str,
               logged_in: bool = False) -> None:
    """Обновить статус подключения; при logged_in — отметить время входа."""
    now = datetime.datetime.now()
    with SessionLocal() as s:
        row = s.execute(
            select(SiteCredential).where(
                SiteCredential.user_id == user_id, SiteCredential.site_id == site_id
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        if logged_in:
            row.last_login_at = now
        row.updated_at = now
        s.commit()


def status(user_id: int, site_id: str) -> dict:
    """Состояние подключения для UI (БЕЗ пароля): есть ли креды, логин, статус."""
    with SessionLocal() as s:
        row = s.execute(
            select(SiteCredential).where(
                SiteCredential.user_id == user_id, SiteCredential.site_id == site_id
            )
        ).scalar_one_or_none()
        if row is None:
            return {"connected": False, "status": STATUS_INVALID,
                    "username": "", "last_login_at": None}
        return {
            "connected": row.status == STATUS_CONNECTED,
            "status": row.status,
            "username": decrypt(row.username_enc),
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        }


def delete(user_id: int, site_id: str) -> None:
    """Удалить креды пользователя для сайта (отключение аккаунта)."""
    with SessionLocal() as s:
        row = s.execute(
            select(SiteCredential).where(
                SiteCredential.user_id == user_id, SiteCredential.site_id == site_id
            )
        ).scalar_one_or_none()
        if row is not None:
            s.delete(row)
            s.commit()
