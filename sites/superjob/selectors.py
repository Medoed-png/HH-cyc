"""Селекторы и URL superjob.ru.

Поиск сверен на живом superjob.ru (2026-06-30). Вход/отклик — пока заглушки
(structure-only), доводятся отдельно. Правьте значения ТОЛЬКО здесь.
"""

BASE = "https://www.superjob.ru"
SEARCH_URL = BASE + "/vacancy/search/"

# --- Авторизация ---
LOGIN_URL = BASE + "/auth/login/"
# Признак входа: в шапке появляется блок личного кабинета.
LOGGED_IN_MARKER = ('[data-qa="account"], a[href*="/auth/logout"], '
                    '.f-test-account, a[href^="/account"]')

# --- Карточка вакансии в выдаче (сверено) ---
# Карточка результата помечена f-test-search-result-item; ссылка-заголовок —
# a с классом f-test-link-<...> и href /vakansii/...-<id>.html; компания —
# f-test-text-vacancy-item-company-name. Зарплата отдельным стабильным классом
# не помечена — берётся регуляркой из текста карточки (см. _parse_card).
VACANCY_CARD = '.f-test-search-result-item'
CARD_TITLE_LINK = 'a[class*="f-test-link-"]'
CARD_COMPANY = '[class*="f-test-text-vacancy-item-company-name"]'
CARD_SALARY = '[class*="f-test-text-company-item-salary"], [class*="alary"]'

# --- Страница вакансии / отклик (структурно; не реализовано до живой сверки) ---
RESPOND_BUTTON = '[class*="f-test-button-Otkliknutsya"], button:has-text("Откликнуться")'
ALREADY_RESPONDED = ':has-text("Вы откликнулись")'
CAPTCHA = '.captcha, iframe[src*="captcha"], [data-qa="captcha"]'

# --- Отклики / ответы работодателей ---
RESPONSES_URL = BASE + "/account/applications/"
