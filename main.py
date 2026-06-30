"""Точка входа: запуск веб-интерфейса HH-бота (http://127.0.0.1:8000)."""
import sys

# Логи содержат юникод (✓, эмодзи, кириллица) — на Windows консоль по умолчанию
# cp1251 и падает на таких символах. Переводим вывод в UTF-8 с заменой.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — поток без reconfigure (старый Python/перенаправление)
        pass

from web.server import main

if __name__ == "__main__":
    main()
