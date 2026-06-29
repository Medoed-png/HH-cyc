"""Аутентификация пользователей сервиса: argon2 + JWT.

Вход в САМ сервис (email + пароль), не в hh.ru. Пароли хранятся как argon2id-хэш
(необратимо). Доступ выдаётся JWT access-токеном (sub = id пользователя). Токен
шлётся в заголовке Authorization: Bearer ... ; для SSE (EventSource не умеет
заголовки) принимаем токен также в query-параметре ?token=.

Refresh-токены и отзыв сессий — отдельный шаг хардненинга (позже). Сейчас
stateless access-токен с TTL; logout = клиент забывает токен.
"""
from __future__ import annotations

import datetime
import os
import secrets

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from hh_bot.db import SessionLocal, User

_ph = PasswordHasher()

_ALGO = "HS256"
ACCESS_TTL = datetime.timedelta(hours=12)


def _load_secret() -> str:
    """Секрет подписи JWT: из env APP_JWT_SECRET либо локальный файл .jwt_secret.

    Файл генерируется один раз и не коммитится (.gitignore), чтобы токены
    переживали перезапуск в dev. В проде секрет задаётся через окружение/Vault.
    """
    env = os.environ.get("APP_JWT_SECRET")
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".jwt_secret")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    val = secrets.token_urlsafe(48)
    with open(path, "w", encoding="utf-8") as f:
        f.write(val)
    return val


_SECRET = _load_secret()


# --- Pydantic-схемы запросов ---

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


# --- Пароли ---

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 — повреждённый хэш и т.п.
        return False


# --- Репозиторий пользователей ---

def get_user_by_email(email: str) -> User | None:
    with SessionLocal() as s:
        return s.execute(
            select(User).where(User.email == email.lower())
        ).scalar_one_or_none()


def get_user_by_id(user_id: int) -> User | None:
    with SessionLocal() as s:
        return s.get(User, user_id)


def create_user(email: str, password: str) -> User:
    user = User(email=email.lower(), password_hash=hash_password(password),
                status="active")
    with SessionLocal() as s:
        s.add(user)
        s.commit()
        s.refresh(user)
    return user


# --- JWT ---

def create_access_token(user_id: int) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + ACCESS_TTL}
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def _user_id_from_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        return int(payload["sub"])
    except Exception:  # noqa: BLE001 — истёк/битый/невалидный токен
        return None


def _extract_token(request: Request) -> str | None:
    """Токен из заголовка Authorization: Bearer ... либо из query ?token=."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def current_user(request: Request) -> User:
    """FastAPI-зависимость: вернуть пользователя по токену или 401."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Требуется вход")
    user_id = _user_id_from_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Недействительный токен")
    user = get_user_by_id(user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Пользователь не найден")
    return user


CurrentUser = Depends(current_user)
