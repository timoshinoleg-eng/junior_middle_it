"""Human-readable RU/EN copy for the Telegram product surface."""
from __future__ import annotations

from typing import Dict

SUPPORTED_LANGUAGES = {"ru", "en"}

CATEGORY_NAMES: Dict[str, Dict[str, str]] = {
    "ru": {
        "development": "Разработка", "qa": "Тестирование", "devops": "DevOps / инфраструктура",
        "data": "Данные / AI", "marketing": "Маркетинг", "sales": "Продажи",
        "pm": "Продукт / проекты", "design": "Дизайн", "support": "Поддержка",
        "security": "Безопасность", "other": "Другое",
    },
    "en": {
        "development": "Development", "qa": "QA / Testing", "devops": "DevOps / Infrastructure",
        "data": "Data / AI", "marketing": "Marketing", "sales": "Sales",
        "pm": "Product / Project", "design": "Design", "support": "Support",
        "security": "Security", "other": "Other",
    },
}


def normalize_language(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else ""


def category_name(category: str, language: str) -> str:
    lang = normalize_language(language) or "ru"
    return CATEGORY_NAMES.get(lang, CATEGORY_NAMES["ru"]).get(str(category), str(category))


def t(language: str, key: str, **kwargs) -> str:
    lang = normalize_language(language) or "ru"
    values = TEXT[lang]
    template = values.get(key) or TEXT["ru"].get(key) or key
    return template.format(**kwargs)


TEXT = {
    "ru": {
        "choose_language": "🌐 Выберите язык интерфейса / Choose interface language",
        "welcome_new": (
            "👋 Я помогу искать Junior/Middle IT-вакансии без лишнего шума.\n\n"
            "Что я умею:\n"
            "• 🔎 подобрать вакансии под твои навыки и зарплату;\n"
            "• 🔔 сразу написать, когда появится подходящая новая вакансия;\n"
            "• 📅 присылать одну удобную подборку раз в день;\n"
            "• 📄 сравнить резюме с конкретной вакансией и подсказать, что улучшить.\n\n"
            "Можно начать без настроек — просто покажу 3 свежие вакансии."
        ),
        "welcome_ready": "👋 С возвращением. Что сделать сейчас?",
        "status": "Твои настройки: профиль — {profile}; новые вакансии сразу — {alerts}; подборка раз в день — {daily}; сохранённых подборок — {searches}.",
        "ready": "готов", "not_ready": "не настроен", "on": "включено", "off": "выключено",
        "find3": "🔎 Показать 3 вакансии", "alerts_on": "🔔 Новые сразу: ВКЛ", "alerts_off": "🔔 Новые сразу: ВЫКЛ",
        "daily_on": "📅 Раз в день: ВКЛ", "daily_off": "📅 Раз в день: ВЫКЛ",
        "my_searches": "💾 Мои подборки ({count})", "preferences": "⚙️ Настроить интересы", "channel": "📢 Канал", "language": "🌐 English",
        "alerts_explain": "🔔 «Новые вакансии сразу» — бот пишет тебе в личку, когда появляется новая вакансия, подходящая под твои настройки. Не больше нескольких лучших совпадений за один цикл проверки.",
        "daily_explain": "📅 «Подборка раз в день» — вместо отдельных уведомлений бот раз в сутки присылает список подходящих вакансий одним сообщением.",
        "searches_title": "💾 Мои подборки", "searches_explain": "Подборка — это сохранённые условия поиска: например «Python + remote» или «QA от $2000». Бот использует их, чтобы выбирать подходящие вакансии.",
        "searches_empty": "Пока нет сохранённых подборок. Можно сохранить текущие настройки или создать подборку после проверки резюме.",
        "save_profile": "💾 Сохранить мои настройки", "back": "← Назад", "deleted": "Подборка удалена", "already_deleted": "Подборка уже удалена",
        "setup1": "⚙️ Шаг 1 из 4. Что тебе интересно?\nВыбери одно или несколько направлений, затем нажми «Далее».",
        "next_salary": "✅ Далее: зарплата", "setup2": "⚙️ Шаг 2 из 4. Какая минимальная зарплата тебе подходит?\nПришли сумму в USD в месяц, например 2000.\nНапиши 0, если зарплату фильтровать не нужно.",
        "bad_salary": "Пришли только число, например 0 или 2000.",
        "setup3": "⚙️ Шаг 3 из 4. Какие навыки у тебя есть?\nНапиши через запятую, например: python, django, postgres\nЕсли не хочешь указывать — отправь «-».",
        "setup4": "⚙️ Шаг 4 из 4. Хочешь получать одну подборку подходящих вакансий каждый день?",
        "yes_daily": "✅ Да, раз в день", "no_daily": "❌ Нет", "profile_ready": "✅ Готово. Настройки сохранены.",
        "looking3": "Ищу 3 свежие вакансии", "no_matches": "Пока нет свежих вакансий под твои настройки. Можно изменить интересы — так шанс совпадения станет выше.",
        "preview_header": "🔎 Вот {count} свежие вакансии. Под каждой можно сохранить вакансию или проверить резюме.",
        "saved": "Сохранено", "saved_next": "💾 Вакансия сохранена. Можно проверить резюме именно под неё или включить уведомления о похожих вакансиях.",
        "check_resume": "📄 Проверить резюме", "manage_new": "🔔 Настроить новые вакансии", "stale": "Эта вакансия уже устарела",
        "resume_prompt": "📄 Проверка резюме · {target}\n\n1) Пришли PDF, DOCX или вставь текст резюме.\n2) Я покажу, какие навыки совпадают с вакансией и чего не хватает.\n3) Дам конкретные советы перед откликом.\n\nРезюме не сохраняется в базе. Файл обрабатывается только для этой проверки. Максимальный размер — 5 МБ.\nОценка ориентировочная: это помощник, а не официальный результат системы работодателя.",
        "profile_title": "👤 Мои настройки", "profile_categories": "Направления", "profile_salary": "Минимальная зарплата", "profile_skills": "Навыки", "profile_daily": "Подборка раз в день", "profile_alerts": "Новые вакансии сразу", "profile_language": "Язык",
        "daily_enabled": "📅 Подборка раз в день включена.", "daily_disabled": "📅 Подборка раз в день выключена.",
        "alerts_enabled": "🔔 Уведомления о новых подходящих вакансиях включены.", "alerts_disabled": "🔔 Уведомления о новых подходящих вакансиях выключены.",
        "apply": "🚀 Откликнуться", "save": "💾 Сохранить", "share": "📤 Поделиться", "details": "⬇️ Подробнее", "collapse": "⬆️ Свернуть", "hide": "🚫 Не показывать: {category}",
    },
    "en": {
        "choose_language": "🌐 Choose interface language / Выберите язык интерфейса",
        "welcome_new": (
            "👋 I help you find Junior/Middle IT jobs without the noise.\n\n"
            "What I can do:\n"
            "• 🔎 find jobs that match your skills and salary target;\n"
            "• 🔔 message you when a new matching job appears;\n"
            "• 📅 send one convenient daily roundup;\n"
            "• 📄 compare your resume with a specific job and suggest improvements.\n\n"
            "You can start without setup — I can simply show 3 fresh jobs."
        ),
        "welcome_ready": "👋 Welcome back. What would you like to do?",
        "status": "Your settings: profile — {profile}; new jobs immediately — {alerts}; daily roundup — {daily}; saved searches — {searches}.",
        "ready": "ready", "not_ready": "not set up", "on": "on", "off": "off",
        "find3": "🔎 Show 3 jobs", "alerts_on": "🔔 New jobs now: ON", "alerts_off": "🔔 New jobs now: OFF",
        "daily_on": "📅 Daily roundup: ON", "daily_off": "📅 Daily roundup: OFF",
        "my_searches": "💾 My searches ({count})", "preferences": "⚙️ Set my preferences", "channel": "📢 Channel", "language": "🌐 Русский",
        "alerts_explain": "🔔 “New jobs now” means I message you when a newly found job matches your preferences. I only send a few of the best matches per check.",
        "daily_explain": "📅 “Daily roundup” means I send matching jobs together once a day instead of sending separate notifications.",
        "searches_title": "💾 My searches", "searches_explain": "A saved search is a set of job preferences, for example “Python + remote” or “QA from $2000”. I use it to choose relevant jobs for you.",
        "searches_empty": "No saved searches yet. You can save your current preferences or create one after a resume check.",
        "save_profile": "💾 Save my preferences", "back": "← Back", "deleted": "Search deleted", "already_deleted": "Search was already deleted",
        "setup1": "⚙️ Step 1 of 4. What kind of work are you interested in?\nChoose one or more areas, then tap Next.",
        "next_salary": "✅ Next: salary", "setup2": "⚙️ Step 2 of 4. What is the minimum monthly salary you want?\nSend an amount in USD, for example 2000.\nSend 0 if you do not want a salary filter.",
        "bad_salary": "Send a number only, for example 0 or 2000.",
        "setup3": "⚙️ Step 3 of 4. What skills do you have?\nSend them separated by commas, for example: python, django, postgres\nSend “-” to skip.",
        "setup4": "⚙️ Step 4 of 4. Would you like one roundup of matching jobs every day?",
        "yes_daily": "✅ Yes, once a day", "no_daily": "❌ No", "profile_ready": "✅ Done. Your preferences are saved.",
        "looking3": "Finding 3 fresh jobs", "no_matches": "There are no fresh jobs matching your preferences right now. You can adjust your preferences to broaden the search.",
        "preview_header": "🔎 Here are {count} fresh jobs. Under each one you can save it or check your resume against it.",
        "saved": "Saved", "saved_next": "💾 Job saved. You can now check your resume for this job or turn on notifications for similar jobs.",
        "check_resume": "📄 Check my resume", "manage_new": "🔔 Set job notifications", "stale": "This job is no longer available",
        "resume_prompt": "📄 Resume check · {target}\n\n1) Send a PDF, DOCX, or paste your resume text.\n2) I will show which skills match the job and what appears to be missing.\n3) I will give specific suggestions before you apply.\n\nYour resume is not stored in the database. The file is processed only for this check. Maximum size: 5 MB.\nThe score is only a practical estimate, not an official employer ATS result.",
        "profile_title": "👤 My preferences", "profile_categories": "Job areas", "profile_salary": "Minimum salary", "profile_skills": "Skills", "profile_daily": "Daily roundup", "profile_alerts": "New jobs immediately", "profile_language": "Language",
        "daily_enabled": "📅 Daily roundup is on.", "daily_disabled": "📅 Daily roundup is off.",
        "alerts_enabled": "🔔 Immediate notifications for matching new jobs are on.", "alerts_disabled": "🔔 Immediate notifications for matching new jobs are off.",
        "apply": "🚀 Apply", "save": "💾 Save", "share": "📤 Share", "details": "⬇️ Details", "collapse": "⬆️ Collapse", "hide": "🚫 Hide: {category}",
    },
}
