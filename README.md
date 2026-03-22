# Junior/Middle IT Job Vacancy Bot v6.0

Telegram-бот для автоматического сбора качественных вакансий для Junior и Middle IT-специалистов с поддержкой 7+ категорий, MarkdownV2 форматирования и inline-кнопок.

## 🚀 Возможности

### Новые функции v6.0:
- 📱 **10 Telegram-каналов** в качестве источников (через Telethon)
- 🧠 **Автоматическая классификация** по категориям (разработка, QA, DevOps, данные, маркетинг, продажи, PM)
- 🎨 **MarkdownV2 форматирование** с эмодзи-категориями
- 🔘 **Inline-кнопки** (Сохранить, Поделиться, Скрыть категорию)
- 💾 **Система избранного** для пользователей
- ⚙️ **Настройка категорий** по предпочтениям

### Основные возможности:
- 🔍 **19 источников вакансий** (9 API + 10 Telegram-каналов)
- 📊 **SQLite база данных** с улучшенной дедупликацией
- 🗑️ **Чёрный список спама** (автоматическая фильтрация)
- 💬 **Публикация в Telegram** с красивым форматированием
- 🔄 **Автообновление** каждые 30 минут
- 👨‍💼 **Админ-команды**: `/status`, `/last N`, `/pause`, `/resume`
- 📝 **Структурированное логирование** (консоль + файл)

## 📦 Источники вакансий

### API источники (9):
1. **Remotive** - качественные англоязычные remote вакансии
2. **RemoteOK** - проверенные компании, только удалёнка
3. **Arbeitnow** - европейские вакансии, без лимитов
4. **Himalayas** - вакансии с зарплатами
5. **We Work Remotely** - высочайшее качество (JSON API)
6. **Jobicy** - remote вакансии
7. **HeadHunter** - публичный эндпоинт, русскоязычный рынок
8. **SuperJob** - требуется бесплатный партнёрский ключ
9. **Adzuna** - требуется бесплатный ключ

### Telegram-каналы (10):
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
ENABLE_TELEGRAM_CHANNELS=true
```

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

### Публичные команды:
- `/start` - Приветственное сообщение
- `/favorites` - Показать сохраненные вакансии
- `/categories` - Настройка категорий

### Админские команды:
- `/status` - Статистика бота
- `/last N` - Последние N вакансий
- `/pause` - Приостановить публикацию
- `/resume` - Возобновить публикацию

## 🗂️ Структура проекта

```
junior_middle_it/
├── channel_bot.py           # Основной файл бота
├── job_classifier.py        # Модуль классификации вакансий
├── telegram_job_parser.py   # Парсер Telegram-каналов
├── message_formatter.py     # Форматирование сообщений
├── migrate_db.py            # Скрипт миграции БД
├── telegram_auth.py         # Утилита авторизации Telegram
├── requirements.txt         # Зависимости
├── .env.example.txt         # Пример конфигурации
└── README.md                # Документация
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
