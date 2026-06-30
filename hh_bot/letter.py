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
    # IT / языки
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Kotlin",
    "Swift", "PHP", "Ruby", "Rust", "Scala", "Elixir", "Dart", "Bash",
    "PowerShell", "Objective-C", "Solidity",
    # IT / базы и хранилища
    "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "ClickHouse",
    "Elasticsearch", "SQLite", "Oracle", "ORM",
    # IT / бэкенд и фреймворки
    "Django", "Flask", "FastAPI", "Spring", "Node.js", "Express", "NestJS",
    "Laravel", "Symfony", ".NET", "ASP.NET", "GraphQL", "gRPC", "WebSocket",
    "RabbitMQ", "Kafka", "Celery", "микросервисы", "многопоточность", "ООП",
    # IT / фронтенд
    "React", "Vue", "Angular", "Svelte", "Next.js", "Redux", "HTML", "CSS",
    "Sass", "Webpack", "Tailwind",
    # IT / DevOps и облака
    "Docker", "Kubernetes", "Linux", "Git", "CI/CD", "AWS", "Azure", "GCP",
    "Terraform", "Ansible", "Jenkins", "GitLab", "Nginx", "Prometheus",
    "Grafana", "Yandex Cloud", "DevOps",
    # IT / данные и ML
    "Pandas", "NumPy", "TensorFlow", "PyTorch", "scikit-learn", "Spark",
    "Airflow", "ETL", "Tableau", "Power BI", "машинное обучение",
    "аналитика данных", "Data Science", "NLP", "computer vision",
    # IT / мобайл и QA
    "Android", "iOS", "Flutter", "React Native", "Selenium", "Pytest",
    "Playwright", "Postman", "автотесты", "тестирование", "unit-тесты",
    # IT / процессы и интеграции
    "REST", "API", "SOAP", "OAuth", "JWT", "Agile", "Scrum", "Kanban", "Jira",
    "Confluence", "code review", "1С", "битрикс",
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


def _pos(title: str, company: str) -> str:
    """«вакансию «title» в компании company» (части опускаются, если пусты)."""
    s = f"«{title}»" if title else "вакансию"
    if company:
        s += f" в компании {company}"
    return s


def _tpl_classic(title, company, skills, extra):
    first = f"Меня заинтересовала ваша вакансия {_pos(title, company)}."
    body = (f"Мой опыт включает {_join(skills)} — это соответствует требованиям, "
            f"указанным в описании вакансии." if skills else
            "Мой опыт и навыки релевантны требованиям вакансии — готов подробно "
            "рассказать о подходящих проектах и задачах.")
    parts = ["Здравствуйте!", first, body]
    if extra:
        parts.append(extra)
    parts.append("Буду рад обсудить детали на собеседовании. Спасибо за внимание!")
    return parts


def _tpl_short(title, company, skills, extra):
    first = f"Добрый день! Откликаюсь на {_pos(title, company)}."
    body = (f"В работе использую {_join(skills)} — это закрывает ключевые требования "
            f"вакансии." if skills else
            "Мои навыки соответствуют требованиям — готов погрузиться в задачи быстро.")
    parts = [first, body]
    if extra:
        parts.append(extra)
    parts.append("Готов обсудить задачи и сроки. Спасибо, что рассмотрели мою кандидатуру!")
    return parts


def _tpl_skills_first(title, company, skills, extra):
    lead = (f"Среди моих навыков — {_join(skills)}, и они хорошо ложатся на вашу "
            f"вакансию." if skills else
            "Мой профиль хорошо подходит под описание вашей вакансии.")
    second = f"Поэтому с интересом откликаюсь на {_pos(title, company)}."
    parts = ["Здравствуйте!", lead, second]
    if extra:
        parts.append(extra)
    parts.append("Буду рад пообщаться на собеседовании. Спасибо за внимание!")
    return parts


_TEMPLATES = [_tpl_classic, _tpl_short, _tpl_skills_first]


def _variant_index(vacancy) -> int:
    """Стабильный выбор шаблона по вакансии (одна вакансия -> один шаблон,
    разные вакансии -> разные шаблоны)."""
    key = str(getattr(vacancy, "vacancy_id", "") or getattr(vacancy, "title", "") or "")
    return sum(ord(c) for c in key) % len(_TEMPLATES)


def build_letter(vacancy, description: str = "", crit=None) -> str:
    """Собрать письмо под вакансию: навыки из описания + один из вариантов шаблона."""
    title = (getattr(vacancy, "title", "") or "").strip()
    company = (getattr(vacancy, "company", "") or "").strip()
    skills = extract_skills(description, crit)
    extra = (getattr(crit, "cover_letter", "") or "").strip() if crit else ""
    parts = _TEMPLATES[_variant_index(vacancy)](title, company, skills, extra)
    return "\n\n".join(parts)
