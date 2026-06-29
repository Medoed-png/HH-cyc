"""Фильтрация вакансий по критериям пользователя."""
from __future__ import annotations

import re

from .config import Criteria
from .models import Vacancy
from .storage import Storage

# Группы синонимов: слова в одной группе считаются эквивалентными при сверке
# названия вакансии с профессией. Можно дополнять.
_SYNONYM_GROUPS = [
    {"python", "питон"},
    {"java"}, {"javascript", "js"}, {"typescript", "ts"},
    {"php"}, {"ruby"}, {"go", "golang"}, {"rust"}, {"swift"}, {"kotlin"},
    {"c++", "cpp"}, {"c#", "csharp", ".net", "dotnet"}, {"scala"}, {"1с", "1c"},
    {"разработчик", "разработка", "разраб", "developer", "dev",
     "programmer", "программист", "engineer", "инженер", "coder",
     "lead", "lead-", "teamlead", "тимлид", "техлид", "руководитель"},
    {"frontend", "фронтенд", "фронт", "front-end"},
    {"backend", "бэкенд", "бекенд", "бэк", "back-end"},
    {"fullstack", "фулстек", "full-stack"},
    {"devops", "девопс"},
    {"тестировщик", "тестирование", "qa", "tester", "автотестировщик"},
    {"аналитик", "analyst", "analytics", "аналитика"},
    {"data scientist", "датасаентист", "ml", "machine learning", "машинное обучение"},
    {"дизайнер", "designer", "дизайн", "design"},
    {"маркетолог", "маркетинг", "marketing", "marketer"},
    {"менеджер", "manager"},
    {"продакт", "product"}, {"проджект", "project"},
    {"продажи", "продаж", "sales", "продавец"},
    {"бухгалтер", "accountant", "бухгалтерия"},
    {"юрист", "lawyer", "юрисконсульт", "legal"},
    {"hr", "рекрутер", "recruiter", "персонал"},
]

# Слова в профессии, которые не несут смысла для отбора (предлоги и т.п.).
_STOP_TOKENS = {"и", "по", "в", "на", "с", "the", "of", "for"}


def _synonyms(token: str) -> set:
    """Вернуть набор синонимов токена (включая сам токен)."""
    tl = token.lower()
    for group in _SYNONYM_GROUPS:
        if tl in group:
            return set(group)
    return {tl}


def _contains(title_lower: str, term: str) -> bool:
    """Есть ли term в названии. Латиница — по границе слова, кириллица — подстрокой."""
    if term.isascii():
        # Границы слова: 'go' не совпадёт с 'category', но совпадёт с 'Go developer'.
        return re.search(r"(?<![a-z0-9+#.])" + re.escape(term) + r"(?![a-z0-9+#])",
                         title_lower) is not None
    # Кириллица — подстрока, чтобы ловить склонения (разработчик/разработчиков).
    return term in title_lower


def title_matches_profession(title: str, profession: str) -> bool:
    """Совпадает ли название вакансии с профессией.

    Каждое значимое слово профессии должно присутствовать в названии
    (само слово или его синоним). Пустая профессия — совпадение есть.
    """
    profession = (profession or "").strip()
    if not profession:
        return True
    title_lower = title.lower()
    # Значимые токены профессии.
    tokens = [t for t in re.split(r"[\s,/]+", profession.lower())
              if t and t not in _STOP_TOKENS]
    if not tokens:
        return True
    for token in tokens:
        variants = _synonyms(token)
        if not any(_contains(title_lower, v) for v in variants):
            return False
    return True


def match(vacancy: Vacancy, crit: Criteria, storage: Storage) -> tuple[bool, str]:
    """Проверить вакансию по критериям.

    Возвращает (подходит, причина_пропуска).
    """
    title_lower = vacancy.title.lower()

    # Уже откликались ранее.
    if storage.is_applied(vacancy.vacancy_id):
        return False, "уже откликались"

    # Строгое совпадение названия с профессией (стек/роль из запроса).
    if getattr(crit, "strict_title_match", True):
        if not title_matches_profession(vacancy.title, vacancy.profession):
            return False, "название не совпадает с профессией"

    # Чёрный список компаний (вакансии этих работодателей пропускаем).
    company_lower = (vacancy.company or "").lower()
    for company in getattr(crit, "company_blacklist", []):
        if company and company.lower() in company_lower:
            return False, f"компания в чёрном списке: {company}"

    # Запрещённые слова.
    for word in crit.exclude_words:
        if word and word.lower() in title_lower:
            return False, f"исключающее слово: {word}"

    # Обязательные слова (если заданы).
    for word in crit.include_words:
        if word and word.lower() not in title_lower:
            return False, f"нет обязательного слова: {word}"

    # Минимальная зарплата (если у вакансии указана нижняя граница).
    if crit.salary_min > 0 and vacancy.salary_from > 0:
        if vacancy.salary_from < crit.salary_min:
            return False, f"зарплата ниже {crit.salary_min}"

    return True, ""


def filter_all(vacancies: list[Vacancy], crit: Criteria,
               storage: Storage) -> list[Vacancy]:
    """Отобрать подходящие вакансии, проставив статусы остальным."""
    from .models import STATUS_SKIPPED

    suitable: list[Vacancy] = []
    for v in vacancies:
        ok, reason = match(v, crit, storage)
        if ok:
            suitable.append(v)
        else:
            v.status = STATUS_SKIPPED
            v.note = reason
    return suitable
