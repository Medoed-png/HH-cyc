"""Автопилот: периодический запуск поиска+откликов по расписанию (in-process).

Раз в check_interval секунд обходит сохранённые критерии всех пользователей
(таблица site_configs) и для тех, у кого включён автопилот и истёк интервал,
сабмитит в SessionManager команды `search` затем `apply` (дневной лимит и паузы
уже соблюдаются в applier.run_applications). Хранит время последнего запуска в
памяти. В облаке (несколько воркеров) заменится на распределённую очередь/планировщик.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import select

from hh_bot import config as config_mod
from hh_bot.db import SessionLocal, SiteConfig


class Autopilot(threading.Thread):
    """Фоновый поток автопилота поверх SessionManager."""

    def __init__(self, manager, check_interval: float = 60.0):
        super().__init__(daemon=True)
        self._manager = manager
        self._check_interval = check_interval
        self._last_run: dict[tuple[int, str], float] = {}
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            time.sleep(self._check_interval)
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — сбой тика не должен валить поток
                print(f"[autopilot] ошибка тика: {e}", flush=True)

    def _tick(self) -> None:
        now = time.monotonic()
        # Снимок (user_id, site_id) с сохранёнными критериями.
        with SessionLocal() as s:
            pairs = [(r.user_id, r.site_id)
                     for r in s.execute(select(SiteConfig)).scalars().all()]
        for user_id, site_id in pairs:
            crit = config_mod.load_for(user_id, site_id)
            if not getattr(crit, "autopilot_enabled", False):
                continue
            interval = max(5, int(crit.autopilot_interval_minutes)) * 60
            last = self._last_run.get((user_id, site_id), 0.0)
            if now - last < interval:
                continue
            self._last_run[(user_id, site_id)] = now
            print(f"[autopilot] запуск u{user_id}/{site_id}", flush=True)
            # Поиск, затем отклик — команды выполняются по очереди в потоке сессии,
            # apply использует найденные поиском вакансии (_last_suitable).
            self._manager.submit(user_id, site_id, "search", crit=crit)
            self._manager.submit(user_id, site_id, "apply", crit=crit)
