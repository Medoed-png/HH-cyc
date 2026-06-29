"""Загрузка и сохранение конфигурации критериев.

Глобальный config.yaml остаётся источником стартовых значений по умолчанию;
рабочие критерии хранятся per-user в таблице site_configs (load_for/save_for).
"""
from __future__ import annotations

import os
import datetime
from dataclasses import dataclass, field, asdict

import yaml

# Путь к config.yaml в корне проекта.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


@dataclass
class Criteria:
    """Критерии поиска и поведения бота (зеркало config.yaml)."""

    professions: list = field(default_factory=lambda: [{"text": "python разработчик"}])
    # Регион — непрозрачная строка, которую интерпретирует адаптер сайта
    # (для hh.ru это id области, напр. "1" = Москва, "113" = вся Россия).
    region: str = "1"
    salary_min: int = 0
    exclude_words: list = field(default_factory=list)
    include_words: list = field(default_factory=list)
    resume_name: str = ""
    cover_letter: str = ""
    daily_limit: int = 150
    delay_seconds: list = field(default_factory=lambda: [20, 45])
    max_pages: int = 5
    # Строгий отбор: показывать только вакансии, где профессия/стек есть в названии.
    strict_title_match: bool = True
    # Фильтры поиска (коды hh.ru; пусто = не фильтровать). experience — одно
    # значение (noExperience/between1And3/between3And6/moreThan6); employment и
    # schedule — списки (full/part/project/volunteer/probation; fullDay/shift/
    # flexible/remote/flyInFlyOut). Интерпретирует адаптер сайта.
    experience: str = ""
    employment: list = field(default_factory=list)
    schedule: list = field(default_factory=list)
    # Чёрный список компаний: вакансии этих работодателей пропускаем.
    company_blacklist: list = field(default_factory=list)
    # Автопилот: периодически сам запускает поиск+отклик (в пределах дневного лимита).
    autopilot_enabled: bool = False
    autopilot_interval_minutes: int = 60

    @property
    def profession_texts(self) -> list:
        """Список поисковых строк из professions."""
        result = []
        for p in self.professions:
            if isinstance(p, dict):
                text = (p.get("text") or "").strip()
            else:
                text = str(p).strip()
            if text:
                result.append(text)
        return result


def load(path: str = CONFIG_PATH) -> Criteria:
    """Загрузить критерии из YAML. При отсутствии файла — значения по умолчанию."""
    if not os.path.exists(path):
        return Criteria()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    crit = Criteria()
    for key, value in data.items():
        if hasattr(crit, key) and value is not None:
            setattr(crit, key, value)
    return crit


def save(crit: Criteria, path: str = CONFIG_PATH) -> None:
    """Сохранить критерии обратно в YAML."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            asdict(crit), f, allow_unicode=True, sort_keys=False, default_flow_style=False
        )


def load_for(user_id: int, site_id: str = "hh") -> Criteria:
    """Критерии конкретного пользователя для сайта.

    Если своих сохранённых критериев ещё нет — отдаём значения из config.yaml как
    стартовые (плавный переход с одиночного конфига на per-user).
    """
    from sqlalchemy import select
    from .db import SessionLocal, SiteConfig

    with SessionLocal() as s:
        row = s.execute(
            select(SiteConfig).where(
                SiteConfig.user_id == user_id, SiteConfig.site_id == site_id
            )
        ).scalar_one_or_none()
        data = row.data if row else None

    if not data:
        return load()  # фолбэк на config.yaml
    crit = Criteria()
    for key, value in data.items():
        if hasattr(crit, key) and value is not None:
            setattr(crit, key, value)
    return crit


def save_for(user_id: int, crit: Criteria, site_id: str = "hh") -> None:
    """Сохранить критерии пользователя для сайта в site_configs."""
    from sqlalchemy import select
    from .db import SessionLocal, SiteConfig

    data = asdict(crit)
    with SessionLocal() as s:
        row = s.execute(
            select(SiteConfig).where(
                SiteConfig.user_id == user_id, SiteConfig.site_id == site_id
            )
        ).scalar_one_or_none()
        now = datetime.datetime.now()
        if row is None:
            s.add(SiteConfig(user_id=user_id, site_id=site_id, data=data, updated_at=now))
        else:
            row.data = data
            row.updated_at = now
        s.commit()


def _split(text: str) -> list:
    """Строку через запятую -> список непустых значений."""
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _to_int(value, default: int = 0) -> int:
    """Оставить из значения только цифры и привести к int."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else default


def from_form(data: dict, base: Criteria | None = None) -> Criteria:
    """Собрать критерии из данных формы веб-интерфейса.

    data: {professions, region (название города), salary_min, exclude_words,
           include_words, resume_name, cover_letter, daily_limit, max_pages}.
    """
    from .cities_list import CITIES

    crit = base or Criteria()
    crit.professions = [{"text": t} for t in _split(data.get("professions", ""))]

    # Название города -> id региона hh.ru (113 = вся Россия). Регион храним
    # строкой: её интерпретирует адаптер сайта (см. SiteAdapter.map_region).
    city_name = (data.get("region") or "").strip()
    city_id = CITIES.get(city_name)
    if city_id is None:
        low = {k.lower(): v for k, v in CITIES.items()}
        city_id = low.get(city_name.lower(), "113")
    crit.region = str(city_id)

    crit.salary_min = _to_int(data.get("salary_min", 0))
    crit.exclude_words = _split(data.get("exclude_words", ""))
    crit.include_words = _split(data.get("include_words", ""))
    crit.resume_name = (data.get("resume_name") or "").strip()
    crit.cover_letter = (data.get("cover_letter") or "").strip()
    crit.daily_limit = _to_int(data.get("daily_limit", 150), 150)
    crit.max_pages = _to_int(data.get("max_pages", 5), 5)

    # Фильтры поиска. experience — строка; employment/schedule — списки кодов
    # (из формы приходят строкой через запятую или списком); blacklist — список.
    crit.experience = (data.get("experience") or "").strip()
    crit.employment = _as_list(data.get("employment"))
    crit.schedule = _as_list(data.get("schedule"))
    crit.company_blacklist = _split(data.get("company_blacklist", ""))

    # Автопилот (чекбокс + интервал в минутах, минимум 5).
    crit.autopilot_enabled = bool(data.get("autopilot_enabled"))
    crit.autopilot_interval_minutes = max(5, _to_int(
        data.get("autopilot_interval_minutes", 60), 60))
    return crit


def _as_list(value) -> list:
    """Привести значение формы к списку непустых строк (список или строка с запятыми)."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return _split(value or "")
