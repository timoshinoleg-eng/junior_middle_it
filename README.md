# Junior/Middle IT Job Vacancy Bot v6.6

Telegram-бот для сбора Junior/Middle remote IT-вакансий: API + ATS + Telegram-каналы, персональные дайджесты, multi-track каналы, source health.

## 🚀 Возможности

### v6.1–v6.6 (growth stack)
- **v6.1** — fuzzy dedup (RapidFuzz), referrals, FloodWait, events
- **v6.2** — `/setup` профиль, personal digest, invite CTA
- **v6.3** — `/stats_growth`, salary magnet, premium refs, realtime alerts
- **v6.4** — multi-track `CHANNEL_ROUTES` (specialty channels by category)
- **v6.5** — free sources: 4dayweek, The Muse, RemoteOK multi-cat, Working Nomads, WWR/Himalayas RSS; `/sources`
- **v6.6** — publish quality score, source auto-skip (`SOURCE_FAIL_SKIP`), title/company normalize

### База v6.0
- 📱 **40+ Telegram-каналов** (Telethon)
- 🧠 **Классификация** по категориям (dev, QA, DevOps, data, design, PM, …)
- 🎨 **MarkdownV2** + inline-кнопки (сохранить / поделиться / скрыть)
- 💾 **Избранное** + настройки категорий
- 🔍 **60+ источников** (API/ATS + TG)
- 📊 **SQLite** dedup + retention

## 📦 Источники вакансий

### Free API / boards (без ключа)
1. **Remotive**, **RemoteOK** (+ multi-cat Dev), **Arbeitnow**, **Himalayas**, **Jobicy**
2. **We Work Remotely** + RSS (programming/design/devops/product)
3. **4dayweek.io**, **The Muse**, **Working Nomads**
4. **DevITJobs UK**, **HN Who is Hiring**
5. **Greenhouse / Lever / Ashby** public boards (defaults expanded in code)

### Keyed / optional
- HeadHunter, SuperJob, Adzuna, Reed, Jooble, FindWork, USAJobs
- Apify actors (USAJobs, All Jobs; paid: CryptocurrencyJobs, Wellfound)

### Telegram-каналы:
1. **remote_developers** - 100% remote, EN/RU
2. **programmer_remote** - remote, EN/RU
3. **digital_nomads** - remote worldwide
4. **itfreelance** - фильтр по ключевым словам
5. **prog_jobs** - структурированные, фильтр remote
6. **coders_jobs** - junior/middle, фильтр remote
7. **design_vacancies** - UX/UI, фильтр remote
8. **project_managers** - PM/Agile, фильтр remote
9. **hitech_jobs** - AI/ML, стартапы, фильтр remote
10. **it_jobs** - агрегатор, усиленная дедупликация

Дополнительно подключены Python, Frontend, Backend, QA, DevOps, Data, Product, Design, GameDev, relocation и junior-focused каналы из прежней VM-конфигурации.

## 🛠️ Установка

### 1. Клонировать репозиторий
```bash
git clone https://github.com/timoshinoleg-eng/junior_middle_it.git
cd junior_middle_it
```

### 2. Создать виртуальное окружение
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 3. Установить зависимости
```bash
pip install -r requirements.txt
```

Для работы с Telegram-каналами также установите:
```bash
pip install telethon
```

### 4. Настроить переменные окружения
```bash
cp .env.example .env
nano .env  # Отредактируйте файл
```

Обязательные переменные:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
CHANNEL_ID=@your_channel_username_or_id
```

Для Telegram-каналов (получите на https://my.telegram.org/apps):
```env
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSION_NAME=job_parser_session
TELEGRAM_SESSION_STRING=optional_string_session_for_serverless
TELEGRAM_PROXY=socks5://127.0.0.1:9050  # опционально, если VM не ходит в Telegram напрямую
TELEGRAM_HOURS_BACK=48
ENABLE_TELEGRAM_CHANNELS=true
```

Опциональные ключи для расширенных источников:
```env
HEADHUNTER_USER_AGENT=RestoBotVacancyParser/1.0 (https://github.com/timoshinoleg-eng/junior_middle_it)
HEADHUNTER_ACCESS_TOKEN=optional_hh_oauth_token
REED_API_KEY=your_reed_api_key
JOOBLE_API_KEY=your_jooble_api_key
FINDWORK_API_TOKEN=your_findwork_token
USAJOBS_API_KEY=your_usajobs_api_key
USAJOBS_USER_AGENT=your.email@example.com
APIFY_API_TOKEN=your_apify_token
APIFY_MAX_ITEMS=30
APIFY_ENABLE_PAID_ACTORS=false
GREENHOUSE_BOARDS=gitlab,canonical,elastic
LEVER_COMPANIES=Instrumentl,2brains,360learning
ASHBY_COMPANIES=cursor,linear,supabase,openai
```

HeadHunter `/vacancies` использует обязательный `User-Agent`. Если API возвращает `403 forbidden` для анонимного поиска вакансий, укажите OAuth token в `HEADHUNTER_ACCESS_TOKEN`.
Apify token включает `Apify USAJobs` и `Apify All Jobs`. `CryptocurrencyJobs` и `Wellfound` включайте через `APIFY_ENABLE_PAID_ACTORS=true` только после аренды соответствующих платных actors в Apify.

### Vercel deployment

Serverless endpoint:

```text
https://junior-middle-it.vercel.app/api/health
https://junior-middle-it.vercel.app/api/cron
```

`/api/cron` защищен `CRON_SECRET`; передавайте его как `Authorization: Bearer <CRON_SECRET>`.

**Cron расписание:** 4 запуска в сутки (00:00, 06:00, 12:00, 18:00 UTC) через GitHub Actions workflow `.github/workflows/vercel-cron.yml`. Vercel Hobby cron ограничен 1 запуском/сутки, поэтому используется внешний триггер.

Для ручного запуска: `workflow_dispatch` в GitHub Actions или прямой GET/POST на `/api/cron` с `Authorization: Bearer <CRON_SECRET>`.

### 5. Выполнить миграцию базы данных
```bash
python migrate_db.py
```

### 6. Авторизоваться в Telegram (для парсинга каналов)
```bash
python telegram_auth.py
```
Введите номер телефона и код подтверждения. Это нужно сделать один раз.

### 7. Запустить бота
```bash
python channel_bot.py
```

## 📚 Команды бота

### Публичные
- `/start` — онбординг + referral deep-link
- `/setup` / `/profile` — профиль (категории, skills, min salary)
- `/digest` — персональный дайджест
- `/alerts` — realtime alerts on/off
- `/ref` — реферальная ссылка
- `/favorites` / `/categories`

### Админ
- `/status` `/last N` `/pause` `/resume`
- `/sources` — health + fail streaks + configured fetchers
- `/tracks` — multi-track routes
- `/stats_growth` — growth metrics

## Ключевые env (полный список — `.env.example.txt`)

```env
TELEGRAM_BOT_TOKEN=
CHANNEL_ID=
# multi-track (v6.4)
CHANNEL_ROUTES=development,qa,devops:@dev;data:@data;*:@main
MULTI_TRACK_ENABLED=true
# free extras (v6.5)
ENABLE_EXTRA_SOURCES=true
ENABLE_RSS_SOURCES=true
# quality ops (v6.6)
SOURCE_FAIL_SKIP=3
ENABLE_SOURCE_DIVERSIFY=true
MAX_POSTS_PER_CYCLE=40
```

## 🗂️ Структура проекта

```
junior_middle_it/
├── channel_bot.py           # pipeline + bot commands
├── growth_utils.py          # fuzzy, salary, multi-track, scores
├── job_sources_extra.py     # free APIs / RSS / source health
├── job_classifier.py
├── telegram_job_parser.py
├── message_formatter.py
├── CHANGELOG_v6.1.md … v6.6.md
├── requirements.txt
├── .env.example.txt
└── README.md
```

## 🎯 Категории вакансий

Бот автоматически классифицирует вакансии по категориям:

| Категория | Эмодзи | Описание |
|-----------|--------|----------|
| development | 💻 | Разработка (Python, JS, Java, etc.) |
| qa | 🧪 | QA / Тестирование |
| devops | 🔧 | DevOps / Инфраструктура |
| data | 📊 | Данные / Аналитика / ML |
| marketing | 📢 | Маркетинг |
| sales | 💼 | Продажи |
| pm | 📋 | Управление проектами |
| design | 🎨 | Дизайн / UX-UI |
| support | 🎧 | Поддержка |
| security | 🔒 | Безопасность |

## 🔧 Настройка cron

Для запуска бота каждые 30 минут:

```bash
crontab -e
```

Добавьте строку:
```bash
*/30 * * * * cd /path/to/junior_middle_it && /path/to/venv/bin/python channel_bot.py >> cron.log 2>&1
```

## 📝 Логирование

Логи сохраняются в файл `bot.log`:
- INFO: основные события
- DEBUG: детальная информация
- ERROR: ошибки

## 🔒 Безопасность

- API ключи хранятся в `.env` файле
- Файл сессии Telegram (`*.session`) добавлен в `.gitignore`
- HTML/Markdown экранирование для защиты от XSS

## 🤝 Расширение функциональности

### Добавление нового API источника:
1. Создайте функцию `fetch_source_name()` в `channel_bot.py`
2. Добавьте функцию в список `api_fetch_functions`

### Добавление нового Telegram-канала:
1. Отредактируйте `TELEGRAM_CHANNELS` в `telegram_job_parser.py`
2. Настройте фильтры и приоритет

### Изменение категорий:
1. Отредактируйте `JobClassifier` в `job_classifier.py`
2. Обновите ключевые слова и веса

## ⚠️ Troubleshooting

### Ошибка "Telethon не установлен"
```bash
pip install telethon
```

### Ошибка "Пользователь не авторизован"
Выполните авторизацию:
```bash
python telegram_auth.py
```

### Ошибка flood control от Telegram
Бот автоматически обрабатывает ограничения с экспоненциальной задержкой.

### Нет вакансий из Telegram-каналов
1. Проверьте `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`
2. Убедитесь, что файл сессии создан
3. Проверьте логи на ошибки доступа

## 📄 Лицензия

MIT License - см. файл [MIT.txt](MIT.txt)

## 🙏 Благодарности

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API
- [Telethon](https://github.com/LonamiWebs/Telethon) - Telegram client library
