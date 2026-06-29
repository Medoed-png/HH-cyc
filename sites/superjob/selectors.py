"""Селекторы и URL superjob.ru.

⚠️ ВНИМАНИЕ: эти селекторы НЕ сверены на живом superjob.ru — они написаны по
типичной структуре сайта и почти наверняка потребуют правки при первом реальном
запуске (как и LOGIN_* у hh.ru). Это второй адаптер для проверки абстракции
SiteAdapter; цель M7 — показать, что добавление сайта не трогает ядро, а не
выдать готовый рабочий скрейпер SuperJob. Правьте значения ТОЛЬКО здесь.
"""

BASE = "https://www.superjob.ru"
SEARCH_URL = BASE + "/vacancy/search/"

# --- Авторизация ---
LOGIN_URL = BASE + "/auth/login/"
# Признак входа: в шапке появляется блок личного кабинета.
LOGGED_IN_MARKER = ('[data-qa="account"], a[href*="/auth/logout"], '
                    '.f-test-account, a[href^="/account"]')

# --- Карточка вакансии в выдаче ---
# SuperJob помечает элементы классами f-test-*; ссылка вакансии ведёт на
# /vakansii/...-<id>.html. Перечисляем варианты через запятую.
VACANCY_CARD = '[class*="f-test-vacancy"], [data-qa="vacancy-serp__vacancy"]'
CARD_TITLE_LINK = 'a[href*="/vakansii/"], a[href*="/vacancy/"], [class*="f-test-link"]'
CARD_COMPANY = '[class*="f-test-text-company-item-name"], a[href*="/clients/"]'
CARD_SALARY = '[class*="f-test-text-company-item-salary"], [class*="salary"]'

# --- Страница вакансии / отклик (структурно; не реализовано до живой сверки) ---
RESPOND_BUTTON = '[class*="f-test-button-Otkliknutsya"], button:has-text("Откликнуться")'
ALREADY_RESPONDED = ':has-text("Вы откликнулись")'
CAPTCHA = '.captcha, iframe[src*="captcha"], [data-qa="captcha"]'

# --- Отклики / ответы работодателей ---
RESPONSES_URL = BASE + "/account/applications/"
