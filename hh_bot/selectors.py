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
# Уже откликнулись — появляется ссылка «Перейти к отклику».
ALREADY_RESPONDED = '[data-qa="vacancy-response-link-view-topic"]'
# Поле сопроводительного письма (появляется ПОСЛЕ клика «Откликнуться»).
COVER_LETTER_INPUT = ('[data-qa="vacancy-response-letter-informer"] textarea, '
                      '[data-qa="textarea-wrapper"] textarea, '
                      'textarea[name="text"]')
# Контейнер информера письма — нужен, чтобы развернуть лениво/свёрнутое поле,
# когда textarea есть в DOM, но ещё не отрисован видимым.
COVER_LETTER_INFORMER = '[data-qa="vacancy-response-letter-informer"]'
# Кнопка отправки сопроводительного письма.
SUBMIT_RESPONSE = ('[data-qa="vacancy-response-letter-submit"], '
                   '[data-qa="vacancy-response-submit-popup"]')
# Попап «дополнительные данные», который иногда перекрывает поле письма.
DATA_COLLECTOR_CLOSE = '[data-qa="additional-data-collector__popup-close"]'
# Выбор резюме (если резюме несколько).
RESUME_SELECT = '[data-qa="resume-select"]'
# Признак, что требуется заполнить тест/доп. вопросы (такие вакансии пропускаем).
RESPONSE_QUESTIONNAIRE = '[data-qa="task-body"], [data-qa="response-question"]'
# Признак капчи.
CAPTCHA = '[data-qa="captcha"], .captcha, iframe[src*="captcha"]'

# --- Чат вакансии (фолбэк доставки сопроводительного письма) ---
# Если inline-поле письма не появилось, письмо отправляем сообщением в чат.
# Кнопка открыть чат ИМЕННО этой вакансии (BUTTON без href). Глобальный
# [data-qa="chatikActivator-button"] для ОТПРАВКИ не используем: он открывает
# виджет со списком чатов и может авто-открыть переписку с другим работодателем —
# письмо ушло бы не в тот чат. Для чтения чатов он есть отдельно (NEG_*).
VACANCY_OPEN_CHAT = '[data-qa="open_chat"]'
# Часть URL iframe, в котором живёт чат (по ней находим нужный frame).
CHAT_FRAME_URL_PART = "chatik.hh.ru/chat"
# Поле ввода и кнопка отправки ВНУТРИ iframe чата.
CHAT_MESSAGE_INPUT = 'textarea[data-qa="chatik-new-message-text"]'
CHAT_SEND_BUTTON = '[data-qa="chatik-do-send-message"]'
# Текст сообщений в чате (подтверждение отправки / защита от дубля).
CHAT_BUBBLE_WRAPPER = '[data-qa="chat-bubble-wrapper"]'
CHAT_BUBBLE_TEXT = '[data-qa="chat-bubble-text"]'

# --- Отклики и приглашения (ответы работодателей) ---
NEGOTIATIONS_URL = BASE + "/applicant/negotiations"
# Признак пустого списка откликов (data-qa может содержать несколько значений).
NEG_EMPTY = ('[data-qa~="negotiations-list-empty"], '
             '[data-qa="negotiations-list-empty-title"]')
# Карточка одного отклика.
NEG_ITEM = '[data-qa="negotiations-item"]'
# Ссылка на вакансию внутри карточки.
NEG_ITEM_VACANCY = 'a[href*="/vacancy/"]'
# Работодатель и дата внутри карточки.
NEG_ITEM_COMPANY = '[data-qa="negotiations-item-company"]'
NEG_ITEM_DATE = '[data-qa="negotiations-item-date"]'
# Теги-статусы (data-qa содержит два значения, поэтому ~=).
NEG_TAG_INTERVIEW = '[data-qa~="negotiations-item-interview"]'
NEG_TAG_DISCARD = '[data-qa~="negotiations-item-discard"]'
NEG_TAG_VIEWED = '[data-qa~="negotiations-item-viewed"]'
NEG_TAG_NOT_VIEWED = '[data-qa~="negotiations-item-not-viewed"]'
# Счётчик непрочитанных сообщений в чате.
NEG_UNREAD_BADGE = '[data-qa="chatikActivator-badge"]'
# Кнопка «Перейти в чат» внутри карточки.
NEG_CHAT_BUTTON = '[data-qa="open_chat"]'
