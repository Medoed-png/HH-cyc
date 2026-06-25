"""Загрузка и сохранение конфигурации критериев."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict

import yaml

# Путь к config.yaml в корне проекта.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


@dataclass
class Criteria:
    """Критерии поиска и поведения бота (зеркало config.yaml)."""

    professions: list = field(default_factory=lambda: [{"text": "python разработчик"}])
    region: int = 1
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
