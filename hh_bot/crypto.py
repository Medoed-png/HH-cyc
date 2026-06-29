"""Обратимое шифрование секретов (креды сайтов, прокси) через Fernet.

В отличие от паролей самого сервиса (argon2, необратимо — см. web/auth.py), креды
для входа на сайты поиска работы нужно вводить в форму браузера, поэтому их надо
уметь расшифровать. Используем симметричный Fernet (AES-128-CBC + HMAC).

Мастер-ключ берётся из env APP_ENCRYPTION_KEY либо из локального файла .enc_key
(генерируется один раз, в .gitignore — как .jwt_secret). В облаке (M8) ключ
придёт из секрет-менеджера/KMS через ту же переменную окружения.

⚠️ Расшифрованные креды живут только в памяти воркера прямо перед логином и
НИКОГДА не логируются.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

# Путь к локальному файлу с ключом (рядом с .jwt_secret, в корне проекта).
_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".enc_key")


def _load_key() -> bytes:
    """Мастер-ключ Fernet: из env APP_ENCRYPTION_KEY либо локальный .enc_key.

    Ключ — urlsafe-base64 32 байта (формат Fernet). Если файла нет — генерируем
    и сохраняем, чтобы зашифрованные данные переживали перезапуск в dev.
    """
    env = os.environ.get("APP_ENCRYPTION_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env
    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            val = f.read().strip()
            if val:
                return val
    val = Fernet.generate_key()
    with open(_KEY_PATH, "wb") as f:
        f.write(val)
    return val


_fernet = Fernet(_load_key())


def encrypt(plaintext: str) -> str:
    """Зашифровать строку -> токен (str, безопасный для хранения в БД)."""
    if plaintext is None:
        plaintext = ""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Расшифровать токен -> исходная строка. Пустой/битый токен -> ''."""
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        # Битый токен / сменился ключ — не роняем приложение, просто нет данных.
        return ""
