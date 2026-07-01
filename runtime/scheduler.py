"""Автопилот: периодический запуск поиска+откликов по расписанию (in-process).

Раз в check_interval секунд обходит сохранённые критерии всех пользователей
(таблица site_configs) и для тех, у кого включён автопилот и истёк интервал,
сабмитит в SessionManager команды `search` затем `apply` (дневной лимит и паузы
уже соблюдаются в applier.run_applications).

Время последнего запуска хранится В БД (site_configs.last_autopilot_at,
wall-clock), поэтому расписание переживает перезапуск процесса и не сбрасывается
на старте. Если дневной лимит уже исчерпан — тик пропускается, чтобы не тратить
сессию браузера впустую. В облаке (несколько воркеров) заменится на
распределённую очередь/планировщик.
"""
from __future__ import annotations

import datetime
import threading
import time

from sqlalchemy import select

from hh_bot import config as config_mod
from hh_bot.db import SessionLocal, SiteConfig
from hh_bot.storage import Storage


def _crit_from_data(data: dict) -> config_mod.Criteria:
    """Собрать Criteria из JSON-данных строки site_configs (как в config.load_for)."""
    crit = config_mod.Criteria()
    for key, value in (data or {}).items():
        if hasattr(crit, key) and value is not None:
            setattr(crit, key, value)
    return crit


class Autopilot(threading.Thread):
    """Фоновый поток автопилота поверх SessionManager."""

    def __init__(self, manager, check_interval: float = 60.0):
        super().__init__(daemon=True)
        self._manager = manager
        self._check_interval = check_interval
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
        now = datetime.datetime.now()
        # Фаза 1: под сессией выбрать площадки, где автопилот включён и по времени
        # из БД истёк интервал. Команды под открытой сессией не сабмитим.
        due = []  # (user_id, site_id, crit)
        with SessionLocal() as s:
            for row in s.execute(select(SiteConfig)).scalars().all():
                crit = _crit_from_data(row.data)
                if not getattr(crit, "autopilot_enabled", False):
                    continue
                interval = max(5, int(crit.autopilot_interval_minutes)) * 60
                last = row.last_autopilot_at
                if last is not None and (now - last).total_seconds() < interval:
                    continue
                due.append((row.user_id, row.site_id, crit))

        # Фаза 2: проверяем дневной лимит (своя сессия Storage), запускаем и
        # фиксируем время запуска в БД.
        for user_id, site_id, crit in due:
            applied = Storage(user_id=user_id, site_id=site_id).applied_today()
            if applied >= max(1, int(crit.daily_limit)):
                # Лимит исчерпан — не запускаем (браузер не трогаем); время НЕ
                # двигаем, чтобы после сброса лимита автопилот сработал.
                continue
            self._mark_run(user_id, site_id, now)
            print(f"[autopilot] запуск u{user_id}/{site_id}", flush=True)
            # Поиск, затем отклик — команды выполняются по очереди в потоке сессии,
            # apply использует найденные поиском вакансии (_last_suitable).
            self._manager.submit(user_id, site_id, "search", crit=crit)
            self._manager.submit(user_id, site_id, "apply", crit=crit)

    def _mark_run(self, user_id: int, site_id: str, now: datetime.datetime) -> None:
        """Записать в БД время запуска автопилота для (user, site)."""
        with SessionLocal() as s:
            row = s.execute(
                select(SiteConfig).where(
                    SiteConfig.user_id == user_id, SiteConfig.site_id == site_id
                )
            ).scalar_one_or_none()
            if row is not None:
                row.last_autopilot_at = now
                s.commit()
