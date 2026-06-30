"""Генерация сопроводительного письма под конкретную вакансию БЕЗ внешних API.

Эвристика (без нейросети): из описания вакансии вытаскиваем знакомые навыки/
ключевые слова (мультидоменный словарь + обязательные слова и профессии из
критериев пользователя) и подставляем их в шаблон вместе с названием вакансии и
компанией. Получается «персональное» письмо под каждую вакансию — для IT, продаж,
логистики, медицины и т.п. Если ничего не распозналось — нейтральный текст.

Личный текст пользователя (crit.cover_letter) добавляется отдельным абзацем.
"""
from __future__ import annotations

import re

# Мультидоменный словарь распознаваемых навыков/ключевых слов (канонический вид
# для подстановки в письмо). Лёгкий, расширяемый. Латиница матчится по границам
# слова, кириллица — как подстрока (ловит склонения).
SKILLS = [
    # IT / разработка
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Kotlin",
    "Swift", "PHP", "Ruby", "Rust", "SQL", "PostgreSQL", "MySQL", "MongoDB",
    "Redis", "Docker", "Kubernetes", "Linux", "Git", "REST", "API", "Django",
    "Flask", "FastAPI", "React", "Vue", "Angular", "Node.js", "Spring",
    "машинное обучение", "аналитика данных", "тестирование", "DevOps", "CI/CD",
    "1С", "битрикс",
    # Офис / общие
    "Excel", "Word", "PowerPoint", "договор", "переговоры", "отчётность",
    "английский язык", "документооборот", "планирование", "бюджет",
    # Продажи / маркетинг
    "продажи", "холодные звонки", "CRM", "клиентская база", "маркетинг",
    "реклама", "SMM", "воронка продаж",
    # Бухгалтерия / финансы
    "бухгалтерия", "налоги", "первичная документация", "финансовый учёт",
    # Логистика / склад / производство
    "склад", "логистика", "погрузка", "водительское удостоверение",
    "складской учёт", "комплектация", "приёмка товара",
    # Медицина / общепит / прочее
    "медицинская книжка", "санитарные нормы", "кассовый аппарат",
    "обслуживание клиентов", "работа в команде",
]

# Несколько вариантов вступления — лёгкая вариативность, выбирается детерминированно.
_INTROS = [
    "Меня заинтересовала ваша вакансия",
    "С интересом откликаюсь на вакансию",
    "Хочу предложить свою кандидатуру на позицию",
]


def _present(skill: str, text_low: str) -> bool:
    s = skill.lower()
    is_latin = bool(re.search(r"[a-z]", s)) and not bool(re.search(r"[а-яё]", s))
    if is_latin:  # границы слова, чтобы 'go'/'api' не ловились внутри слов
        return re.search(r"(?<![a-z0-9+#.])" + re.escape(s) + r"(?![a-z0-9+#])",
                         text_low) is not None
    return s in text_low


def extract_skills(description: str, crit=None, limit: int = 6) -> list[str]:
    """Навыки/ключевые слова из описания: словарь + обязательные слова/профессии."""
    low = (description or "").lower()
    found: list[str] = []
    seen: set[str] = set()

    canon = {s.lower(): s for s in SKILLS}  # нормализация регистра: python -> Python

    def add(label: str):
        label = canon.get(label.lower(), label)
        key = label.lower()
        if key not in seen and label:
            seen.add(key)
            found.append(label)

    # Сначала обязательные слова пользователя (если они есть в описании) — они
    # осмысленные и приоритетные. Профессии не берём: это роли («разработчик»),
    # а не навыки.
    if crit is not None:
        for w in list(getattr(crit, "include_words", []) or []):
            if w and _present(w, low):
                add(w)
    # Затем общий словарь навыков.
    for sk in SKILLS:
        if _present(sk, low):
            add(sk)
    return found[:limit]


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " и " + items[-1]


def build_letter(vacancy, description: str = "", crit=None) -> str:
    """Собрать письмо под вакансию из её названия/компании и навыков из описания."""
    title = (getattr(vacancy, "title", "") or "").strip()
    company = (getattr(vacancy, "company", "") or "").strip()
    skills = extract_skills(description, crit)

    intro = _INTROS[len(title) % len(_INTROS)]
    first = f"{intro} «{title}»" if title else intro
    if company:
        first += f" в компании {company}"
    first += "."

    if skills:
        body = (f"Мой опыт включает {_join(skills)} — это соответствует требованиям, "
                f"указанным в описании вакансии.")
    else:
        body = ("Мой опыт и навыки релевантны требованиям вакансии — готов подробно "
                "рассказать о подходящих проектах и задачах.")

    parts = ["Здравствуйте!", first, body]
    extra = (getattr(crit, "cover_letter", "") or "").strip() if crit else ""
    if extra:
        parts.append(extra)
    parts.append("Буду рад обсудить детали на собеседовании. Спасибо за внимание!")
    return "\n\n".join(parts)
