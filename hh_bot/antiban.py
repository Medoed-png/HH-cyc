"""Анти-бан: человекоподобное поведение и stealth-настройки браузера.

Единое место, через которое адаптеры/воркер выполняют «горячие» действия вместо
сырых кликов и мгновенного ввода. Цель — снизить машинный след автоматизации:
  • stealth при запуске контекста (реалистичный UA/локаль/таймзона, маскировка
    navigator.webdriver, человеческий джиттер вьюпорта, опц. прокси);
  • human_click — подвод курсора по кривой Безье к случайной точке элемента;
  • human_type — посимвольный ввод со случайными задержками;
  • human_pause — пауза со случайной длительностью вместо фиксированной.

Все действия имеют надёжный фолбэк на обычный Playwright-клик/ввод, чтобы анти-бан
никогда не ломал основной сценарий (если bbox недоступен или координатный клик
не прошёл). Прокси (M6b) и распределённый rate-limiter (M8/Redis) подключатся
сюда же позже; пока модуль self-contained, без внешних зависимостей.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any

# Реалистичный UA свежего Chrome под Windows (синхронизируйте мажор с реальным
# Chromium Playwright при больших расхождениях, иначе UA выглядит подозрительно).
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Флаги запуска: гасим автоматизационные признаки.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--no-first-run",
]

# Init-скрипт: маскирует следы headless/automation до загрузки страницы.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


def context_options(proxy_url: str | None = None) -> dict[str, Any]:
    """Kwargs для launch_persistent_context: stealth-UA/локаль/таймзона/вьюпорт.

    Вьюпорт с небольшим джиттером, чтобы размер окна не был одинаков у всех
    сессий. proxy_url (M6b) — строка вида "http://user:pass@host:port"; None —
    без прокси.
    """
    opts: dict[str, Any] = {
        "args": list(LAUNCH_ARGS),
        "user_agent": DEFAULT_UA,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "viewport": {
            "width": 1280 + random.randint(-40, 40),
            "height": 900 + random.randint(-30, 30),
        },
    }
    if proxy_url:
        opts["proxy"] = {"server": proxy_url}
    return opts


def apply_stealth(context) -> None:
    """Навесить stealth-init-скрипт на контекст (действует на все страницы)."""
    try:
        context.add_init_script(STEALTH_JS)
    except Exception:  # noqa: BLE001 — stealth не критичен для работы
        pass


def human_pause(min_s: float = 2.0, max_s: float = 8.0) -> None:
    """Пауза случайной длительности (имитация человеческого темпа)."""
    time.sleep(random.uniform(min_s, max_s))


def _bezier_points(x0: float, y0: float, x1: float, y1: float, steps: int):
    """Точки квадратичной кривой Безье от (x0,y0) к (x1,y1) со случайным контролем."""
    cx = (x0 + x1) / 2 + random.uniform(-60, 60)
    cy = (y0 + y1) / 2 + random.uniform(-60, 60)
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt * mt * x0 + 2 * mt * t * cx + t * t * x1
        y = mt * mt * y0 + 2 * mt * t * cy + t * t * y1
        pts.append((x, y))
    return pts


def _move_curve(page, x: float, y: float) -> None:
    """Подвести курсор к (x,y) по кривой Безье с джиттером по шагам."""
    start = getattr(page, "_ab_mouse", (random.uniform(0, 300), random.uniform(0, 300)))
    steps = random.randint(12, 22)
    for px, py in _bezier_points(start[0], start[1], x, y, steps):
        page.mouse.move(px + random.uniform(-1.5, 1.5), py + random.uniform(-1.5, 1.5))
        time.sleep(random.uniform(0.004, 0.018))
    page._ab_mouse = (x, y)


def human_click(page, element) -> None:
    """Кликнуть по элементу: подвод курсора кривой + клик в случайную точку bbox.

    При любой проблеме (нет bbox, элемент в iframe со смещением и т.п.) —
    надёжный фолбэк на обычный element.click(), чтобы не сорвать сценарий.
    """
    try:
        element.scroll_into_view_if_needed(timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    try:
        box = element.bounding_box()
        if box and box.get("width") and box.get("height"):
            tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            _move_curve(page, tx, ty)
            time.sleep(random.uniform(0.04, 0.12))
            page.mouse.click(tx, ty)
            return
    except Exception:  # noqa: BLE001
        pass
    element.click()  # фолбэк: actionable-клик Playwright


def human_type(page, element, text: str) -> None:
    """Ввести текст посимвольно со случайными задержками (после фокуса элемента)."""
    text = text or ""
    try:
        element.scroll_into_view_if_needed(timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    try:
        element.click()
    except Exception:  # noqa: BLE001
        pass
    try:
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(random.uniform(0.02, 0.10))
        return
    except Exception:  # noqa: BLE001
        pass
    # Фолбэк: гарантированно вписать значение, если посимвольный ввод не прошёл.
    try:
        element.fill(text)
    except Exception:  # noqa: BLE001
        pass
