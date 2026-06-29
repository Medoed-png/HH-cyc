"""Offline smoke-тест ядра парсинга и фильтрации (без сети и браузера).

Запуск:  python tests/smoke_test.py
Нужен как «сеть безопасности» (M0): даёт быстро убедиться, что перенос модулей
в адаптеры (M1) не сломал разбор выдачи и отбор вакансий. Чистые функции
тестируются напрямую; парсинг карточек — через крошечный fake-DOM, повторяющий
тот минимум интерфейса Playwright, который реально использует search.parse_cards
(query_selector / query_selector_all / inner_text / get_attribute).
"""
from __future__ import annotations

import os
import sys

# Позволяем запускать файл напрямую: добавляем корень проекта в путь импорта.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hh_bot import search, filters
from hh_bot.config import Criteria
from hh_bot.models import Vacancy, STATUS_SKIPPED
from hh_bot import selectors


# --- Крошечный fake-DOM (только то, что использует search.py) ------------------

class FakeElement:
    def __init__(self, text: str = "", attrs: dict | None = None):
        self._text = text
        self._attrs = attrs or {}

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


class FakeCard:
    """Карточка вакансии: сопоставление селектор -> элемент + общий текст."""

    def __init__(self, by_selector: dict, full_text: str = ""):
        self._by_selector = by_selector
        self._full_text = full_text

    def query_selector(self, selector: str):
        return self._by_selector.get(selector)

    def inner_text(self) -> str:
        return self._full_text


class FakePage:
    def __init__(self, cards: list[FakeCard]):
        self._cards = cards

    def query_selector_all(self, selector: str):
        # В parse_cards единственный query_selector_all — по VACANCY_CARD.
        return self._cards if selector == selectors.VACANCY_CARD else []


class FakeStorage:
    """Заглушка истории откликов: задаём, какие id уже отправлены."""

    def __init__(self, applied: set | None = None):
        self._applied = applied or set()

    def is_applied(self, vacancy_id: str) -> bool:
        return vacancy_id in self._applied


# --- Утилита прогона тестов ---------------------------------------------------

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


# --- Тесты: парсинг зарплаты (чистые функции search.py) -----------------------

def test_salary_parsing() -> None:
    print("[salary]")
    check("от X", search._parse_salary_from("от 100 000 ₽") == 100000)
    check("диапазон -> нижняя", search._parse_salary_from("250 000 – 320 000 ₽") == 250000)
    check("пусто -> 0", search._parse_salary_from("") == 0)
    check("нет цифр -> 0", search._parse_salary_from("по договорённости") == 0)
    # Поиск текста зарплаты в тексте карточки.
    found = search._find_salary_text("Python разработчик от 200 000 ₽ Москва")
    check("находит текст зарплаты", "200 000" in found and "₽" in found, found)


# --- Тесты: совпадение названия с профессией (filters.py) ----------------------

def test_title_matching() -> None:
    print("[title-match]")
    tm = filters.title_matches_profession
    check("python синоним", tm("Питон разработчик", "python разработчик"))
    check("js == javascript", tm("JavaScript Developer", "js разработчик"))
    check("не совпадает", not tm("Бухгалтер", "python разработчик"))
    check("пустая профессия -> ок", tm("Любая вакансия", ""))
    check("граница слова: go не в category", not tm("Category manager", "go"))


# --- Тесты: фильтрация (filters.match / filter_all) ---------------------------

def _vac(vid="1", title="Python разработчик", salary_from=200000, profession="python разработчик"):
    return Vacancy(vacancy_id=vid, title=title, company="ООО Ромашка",
                   url=f"https://hh.ru/vacancy/{vid}", salary="", salary_from=salary_from,
                   profession=profession)


def test_filtering() -> None:
    print("[filter]")
    crit = Criteria(professions=[{"text": "python разработчик"}], salary_min=150000,
                    exclude_words=["1с"], strict_title_match=True)
    st = FakeStorage()

    ok, _ = filters.match(_vac(), crit, st)
    check("подходящая проходит", ok)

    ok, reason = filters.match(_vac(salary_from=100000), crit, st)
    check("ниже зарплаты отсев", not ok and "зарплата" in reason, reason)

    ok, reason = filters.match(_vac(title="1С разработчик", profession="python разработчик"),
                               crit, st)
    check("исключающее слово / название отсев", not ok, reason)

    ok, reason = filters.match(_vac(vid="42"), crit, FakeStorage({"42"}))
    check("уже откликались отсев", not ok and "уже" in reason, reason)

    # filter_all проставляет статус пропущенным.
    good, bad = _vac(vid="10"), _vac(vid="11", salary_from=50000)
    suitable = filters.filter_all([good, bad], crit, st)
    check("filter_all отбирает подходящие", suitable == [good])
    check("filter_all метит пропущенные", bad.status == STATUS_SKIPPED and bad.note)


# --- Тесты: разбор карточки выдачи через fake-DOM (search.parse_cards) ---------

def test_parse_cards() -> None:
    print("[parse-cards]")
    card = FakeCard({
        selectors.CARD_TITLE_LINK: FakeElement(
            "Python разработчик", {"href": "/vacancy/12345678?query=abc"}),
        selectors.CARD_COMPANY: FakeElement("ООО Ромашка"),
        selectors.CARD_SALARY: FakeElement("от 200 000 ₽"),
    }, full_text="Python разработчик ООО Ромашка от 200 000 ₽")
    page = FakePage([card])

    vacs = search.parse_cards(page, "python разработчик")
    check("распарсена 1 карточка", len(vacs) == 1, str(len(vacs)))
    v = vacs[0]
    check("id из href", v.vacancy_id == "12345678", v.vacancy_id)
    check("url без query", v.url == "https://hh.ru/vacancy/12345678", v.url)
    check("title", v.title == "Python разработчик", v.title)
    check("company", v.company == "ООО Ромашка", v.company)
    check("salary_from", v.salary_from == 200000, str(v.salary_from))

    # Карточка без заголовка/ссылки -> пропускается (None).
    empty = FakeCard({})
    check("карточка без title пропущена", not search.parse_cards(FakePage([empty]), "x"))


# --- Тесты: шов адаптера сайта (sites/) ---------------------------------------

def test_site_adapter() -> None:
    print("[adapter]")
    import sites
    a = sites.get_adapter("hh")
    check("id/имя адаптера", a.site_id == "hh" and a.display_name == "hh.ru")
    check("base_url", a.base_url == "https://hh.ru", a.base_url)
    check("map_region город -> id", a.map_region("Москва") == "1", a.map_region("Москва"))
    check("map_region неизвестный -> 113", a.map_region("Нету-такого") == "113")
    check("suggest_cities offline", "Москва" in a.suggest_cities("Моск"))
    names = [f["name"] for f in a.config_schema()]
    check("config_schema содержит поля", "professions" in names and "region" in names)
    check("реестр сайтов", any(s["id"] == "hh" for s in sites.list_sites()))
    # Неизвестный сайт -> ошибка.
    try:
        sites.get_adapter("nope")
        check("неизвестный сайт -> ошибка", False)
    except KeyError:
        check("неизвестный сайт -> ошибка", True)


def main() -> int:
    test_salary_parsing()
    test_title_matching()
    test_filtering()
    test_parse_cards()
    test_site_adapter()
    print(f"\nИтого: {_passed} ok, {_failed} fail")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
