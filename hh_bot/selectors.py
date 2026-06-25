"""Все селекторы и URL hh.ru в одном месте.

Вёрстка hh.ru периодически меняется. Если бот перестал находить элементы —
правьте значения ТОЛЬКО здесь. Используются преимущественно data-qa атрибуты,
они стабильнее CSS-классов.
"""

BASE = "https://hh.ru"
SEARCH_URL = BASE + "/search/vacancy"

# --- Авторизация ---
# Признак, что пользователь залогинен (присутствует меню пользователя).
LOGGED_IN_MARKER = '[data-qa="mainmenu_applicantProfile"], [data-qa="mainmenu_myResumes"]'

# --- Карточка вакансии в выдаче ---
VACANCY_CARD = '[data-qa="vacancy-serp__vacancy"], [data-qa="vacancy-serp__vacancy_premium"]'
CARD_TITLE_LINK = '[data-qa="serp-item__title"]'
CARD_COMPANY = '[data-qa="vacancy-serp__vacancy-employer"]'
# У зарплаты в новой вёрстке нет своего data-qa — достаём её регуляркой
# из текста карточки (см. search.py). Селектор оставлен как запасной.
CARD_SALARY = '[data-qa="vacancy-serp__vacancy-compensation"]'

# --- Страница вакансии / отклик ---
RESPOND_BUTTON = '[data-qa="vacancy-response-link-top"], [data-qa="vacancy-response-button"]'
# Уже откликнулись — кнопка меняет вид.
ALREADY_RESPONDED = '[data-qa="vacancy-response-link-view-topic"]'
# Поле сопроводительного письма (в попапе или на отдельной странице отклика).
COVER_LETTER_TOGGLE = '[data-qa="vacancy-response-letter-toggle"]'
COVER_LETTER_INPUT = '[data-qa="vacancy-response-popup-form-letter-input"], textarea[name="text"]'
# Кнопка подтверждения отклика в попапе.
SUBMIT_RESPONSE = '[data-qa="vacancy-response-submit-popup"], [data-qa="vacancy-response-letter-submit"]'
# Выбор резюме (если резюме несколько).
RESUME_SELECT = '[data-qa="resume-select"]'
# Признак, что требуется заполнить тест/доп. вопросы (такие вакансии пропускаем).
RESPONSE_QUESTIONNAIRE = '[data-qa="task-body"], [data-qa="response-question"]'
# Признак капчи.
CAPTCHA = '[data-qa="captcha"], .captcha, iframe[src*="captcha"]'
