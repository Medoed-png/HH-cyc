"""Живые подсказки профессий от hh.ru (как в их поисковой строке).

Использует публичный сервис подсказок hh.ru:
    https://api.hh.ru/suggests/vacancy_search_keyword?text=...
Возвращает готовые фразы: «Python разработчик», «Python стажер», «Python backend»…
При отсутствии интернета молча возвращает пустой список (вызывающая сторона
сделает откат на встроенный справочник профессий).
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request

_ENDPOINT = "https://api.hh.ru/suggests/vacancy_search_keyword"
_HEADERS = {"User-Agent": "Mozilla/5.0 (HH-bot suggest)"}

# Кэш успешных ответов: запрос -> список подсказок.
_cache: dict[str, list] = {}

# SSL-контекст. На некоторых сборках Python (особенно macOS) нет корневых
# сертификатов — тогда проверка падает. Пытаемся взять сертификаты из certifi,
# иначе используем контекст без проверки (эндпоинт публичный, только чтение).
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl._create_unverified_context()


def _open(url: str, timeout: float):
    """Открыть URL; при ошибке сертификата — повтор без проверки."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


def fetch_suggestions(text: str, timeout: float = 2.0) -> list[str]:
    """Получить подсказки профессий по введённому тексту.

    Возвращает список фраз. При любой ошибке/таймауте — пустой список.
    """
    text = (text or "").strip()
    if len(text) < 1:
        return []
    if text in _cache:
        return _cache[text]

    url = _ENDPOINT + "?" + urllib.parse.urlencode({"text": text})
    try:
        with _open(url, timeout) as resp:
            data = json.load(resp)
        items = [i.get("text", "").strip()
                 for i in data.get("items", []) if i.get("text")]
    except Exception:
        return []  # нет сети / таймаут — без кэширования, чтобы повторить позже

    if items:
        _cache[text] = items
    return items
