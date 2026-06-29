"""Уведомления в Telegram через Bot API (без внешних зависимостей).

Токен бота — общий, из env TELEGRAM_BOT_TOKEN (создаётся через @BotFather).
chat_id — у каждого пользователя свой (узнаётся через @userinfobot или getUpdates).
Если токен или chat_id не заданы — отправка тихо пропускается.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def is_configured() -> bool:
    """Задан ли токен бота на сервере."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())


def send_telegram(chat_id: str, text: str) -> bool:
    """Отправить сообщение в Telegram. Возвращает True при успехе."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode())
            return bool(body.get("ok"))
    except Exception:  # noqa: BLE001 — сбой уведомления не должен влиять на бота
        return False
