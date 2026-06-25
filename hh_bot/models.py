"""Модели данных."""
from __future__ import annotations

from dataclasses import dataclass


# Возможные статусы вакансии в ходе работы бота.
STATUS_FOUND = "найдена"
STATUS_APPLIED = "откликнулись"
STATUS_SKIPPED = "пропущена"
STATUS_ERROR = "ошибка"


@dataclass
class Vacancy:
    """Одна вакансия, распарсенная из выдачи hh.ru."""

    vacancy_id: str
    title: str
    company: str
    url: str
    salary: str = ""          # текст зарплаты как на сайте ("от 100 000 ₽")
    salary_from: int = 0      # распарсенная нижняя граница, 0 если не указана
    profession: str = ""      # по какому поисковому запросу найдена
    status: str = STATUS_FOUND
    note: str = ""            # причина пропуска / текст ошибки

    def as_row(self) -> tuple:
        """Кортеж для таблицы GUI."""
        return (self.title, self.company, self.salary, self.status, self.note)
