# Junior/Middle IT Job Vacancy Bot — v7.0 (AI Career Copilot)

Telegram-бот для сбора Junior/Middle remote IT-вакансий: 40+ каналов и 60+
источников (API/ATS/RSS), персональные дайджесты, Saved Searches, Resume Match и
**AI Career Copilot** — персональный разбор вакансии, CV под вакансию, трекер
откликов, upskill-радар и Telegram Mini App.

Версия 7.0 добавляет Copilot поверх действующей архитектуры: Postgres в
Supabase, Edge-мост `growth-proxy`, webhook-рантайм бота и единственный
издатель в канал из Vercel production. Ниже описана **фактическая** схема
работы, а не состояние v6.x.

---

## 🏗️ Архитектура

```text
40+ Telegram-каналов (Telethon) + 60+ API/ATS/RSS источников
                │
                ▼
        сбор и фильтрация вакансий
                │
                ▼
   Vercel function /api/cron  (4 запуска в сутки, GitHub Actions)
                │
                ▼
   Edge-мост growth-proxy → Supabase Postgres (growth_job_payloads)
                │
                ▼
   publication_authority.py → единственный издатель в канал (Vercel production)
                │
                ▼
   /api/delivery (ежедневно 09:00 UTC) → канальный дайджест + персональные
                │
                ▼
   /api/webhook ← Telegram  →  interactive_webhook_runtime → JobBot
                │
                ▼
   Supabase Postgres: growth_user_settings, growth_saved_searches,
   growth_events, growth_delivery_receipts, growth_telegram_updates,
   growth_copilot_*
```

### Ключевые правила

| Правило | Как реализовано |
|---|---|
| Durable state обязателен | `REQUIRE_DURABLE_GROWTH=true`; без Postgres рантайм падает (fail-closed) |
| Писать в канал может только Vercel production | `publication_authority.py`: публикация только при `VERCEL=1` и `VERCEL_ENV=production` |
| SQL идёт только через Edge-мост | `growth-proxy` (ключ) и `interactive-growth-proxy` (токен бота) принимают только relations `growth_*`, одно утверждение, без `--`, `/*`, `;` и служебных слов |
| Один планировщик | GitHub Actions: `vercel-cron.yml` (сбор), `delivery-cron.yml` (доставка), `funnel-audit.yml` (отчёт) |
| Один Update = один HTTP-запрос | дедуп через леас-лэджер `growth_telegram_updates` (`claimed/processing/processed`, lease 10 минут) |

### Режимы рантайма

- **Vercel (webhook)** — `api/webhook.py` → `localized_webhook_runtime` →
  `interactive_webhook_runtime.build_runtime()`. Секрет `X-Telegram-Bot-Api-Secret-Token`.
- **Render (long polling)** — тот же `build_runtime()`, но опрос Telegram.
  В текущем проде этот режим **выключен** (нет `TELEGRAM_BOT_TOKEN`): интерактив
  рантайм живёт в Vercel webhook, Render-инстанс fail-closed.

---

## 🤖 AI Career Copilot (v7.0)

Copilot — это надстройка над детерминированным ядром. **Ядро никогда не
вызывает сеть и модель**: все оценки воспроизводимы и офлайн. LLM подключается
только по явной кнопке пользователя и за отдельным флагом.

| Поверхность | Что делает | Флаг |
|---|---|---|
| `/copilot`, кнопка «✨ Разбор вакансии» | Персональный fit-score, честный разбор грейда, контакт HR, сохранение поиска | `COPILOT_ENABLED` |
| «🧾 Собрать CV под вакансию» | PDF-резюме на `fpdf2` + ATS-проверка, кэш `file_id`, один кредит | `COPILOT_PDF_ENABLED` |
| «🤖 Разобрать глубже (AI)» | Опциональный LLM-разбор, деградация к детерминированному отчёту | `COPILOT_LLM_ENABLED` + ключ |
| Пересылка вакансии в личку | Тот же разбор для пересланного поста, дедуп по хэшу текста | `COPILOT_FORWARD_ENABLED` |
| «✉️ Отклик отправлен» | Трекер отклика, напоминание через 7 дней, исход «интервью/тишина/отказ» | `COPILOT_OUTCOME_ENABLED` |
| `/upskill` | Радар по базе вакансий: сколько вакансий закрывает стек и чего не хватает | `COPILOT_UPSKILL_ENABLED` |
| Mini App `/copilot` | Три вкладки (Fit / Резюме / Поиск), доступ только по подписи Telegram | `COPILOT_WEBAPP_ENABLED` + URL |

Подробности: [`docs/COPIOT.md`](docs/COPIOT.md). Приватность и недоверенный
ввод: [`SECURITY.md`](SECURITY.md).

---

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

### Telegram-каналы (40+)
`remote_developers`, `programmer_remote`, `digital_nomads`, `itfreelance`,
`prog_jobs`, `coders_jobs`, `design_vacancies`, `project_managers`,
`hitech_jobs`, `it_jobs` и подключённые Python/Frontend/Backend/QA/DevOps/Data/
Product/Design/GameDev/relocation/junior каналы.

---

## 🛠️ Установка и запуск

### 1. Клонировать и создать окружение
```bash
git clone https://github.com/timoshinoleg-eng/junior_middle_it.git
cd junior_middle_it
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Переменные окружения
Полный список с комментариями — `.env.example.txt`. Минимум:

```env
TELEGRAM_BOT_TOKEN=            # токен бота (обязателен для webhook и Mini App)
CHANNEL_ID=                    # канал-издатель
BOT_USERNAME=junior_jobs_channel_bot
ADMIN_USER_ID=                 # доступ к /stats_growth и служебным командам

# durable growth state (Supabase Postgres через Edge-мост)
GROWTH_DATABASE_URL=https://<project>.supabase.co/functions/v1/interactive-growth-proxy
GROWTH_HTTP_AUTH_MODE=telegram
REQUIRE_DURABLE_GROWTH=true
GROWTH_PUBLIC_URL=https://<project>.supabase.co/functions/v1/growth-proxy
GROWTH_HTTP_KEY=               # ключ Edge-моста (только в секретах)

# доставка
CRON_SECRET=                   # Bearer-токен для /api/cron и /api/delivery
```

Ключи API-источников (опционально): `HEADHUNTER_ACCESS_TOKEN`, `REED_API_KEY`,
`JOOBLE_API_KEY`, `FINDWORK_API_TOKEN`, `USAJOBS_API_KEY`, `APIFY_API_TOKEN`.
HeadHunter требует `User-Agent`; Apify-paid акторы включаются только
`APIFY_ENABLE_PAID_ACTORS=true`.

### 3. База данных
Схема — **Supabase Postgres**, миграции в `supabase/migrations/`. Применяются
на стороне Supabase (в репозитории лежат только SQL-файлы, идемпотентные,
`CREATE TABLE IF NOT EXISTS` + RLS + `REVOKE` для `anon`/`authenticated`).

Локальный SQLite — только dev-фолбэк, в проде его быть не должно.

### 4. Авторизация Telethon (нужна только для парсинга каналов)
```bash
python telegram_auth.py
```

### 5. Локальный запуск интерактивного бота
```bash
python channel_bot.py           # long polling, требует durable Postgres
```

---

## ☁️ Деплой (Vercel + GitHub Actions)

Функции в `api/`:

| Endpoint | Назначение | Защита |
|---|---|---|
| `/api/site` | публичный лендинг | — |
| `/api/health` | health-контракт | — |
| `/api/cron` | сбор вакансий и публикация | `Authorization: Bearer CRON_SECRET` |
| `/api/delivery` | ежедневный канальный + персональный дайджест | `Authorization: Bearer CRON_SECRET` |
| `/api/webhook` | приём Telegram-обновлений | `X-Telegram-Bot-Api-Secret-Token` |
| `/api/copilot` | API Mini App (fit, поиски) | подпись `initData` Telegram |
| `/api/copilot/pdf` | CV под вакансию | подпись `initData` Telegram |
| `/api/robots.txt` | robots.txt | — |

Планировщики (GitHub Actions, без нового планировщика):

| Workflow | Расписание | Что делает |
|---|---|---|
| `vercel-cron.yml` | 00:00, 06:00, 12:00, 18:00 UTC | дёргает `/api/cron` |
| `delivery-cron.yml` | ежедневно 09:00 UTC | дёргает `/api/delivery` |
| `funnel-audit.yml` | ежедневно | отчёт по воронке |
| `production-smoke.yml` | после зелёного CI в `main` | smoke прод-контракта |
| `ci.yml` | PR и `main` | тесты на Python 3.12 и 3.13 |

Статика Mini App лежит в `miniapp/`, маршрут `/copilot` задан rewrite-ом в
`vercel.json`.

---

## 📚 Команды бота

### Публичные
- `/start` — онбординг, referral и campaign deep-links
- `/setup` / `/profile` — профиль (категории, навыки, минимальная зарплата)
- `/digest` — персональный дайджест
- `/alerts` — realtime-алерты вкл/выкл
- `/ref` — реферальная ссылка
- `/favorites` / `/categories` / `/tracks` / `/sources` / `/status` / `/last`
- `/copilot` — персональный разбор вакансии
- `/upskill` — радар по стеку

### Админ
- `/pause` / `/resume` — пауза публикаций
- `/stats_growth` — воронка и метрики Copilot

---

## 🗂️ Структура проекта

```text
junior_middle_it/
├── api/                       # Vercel serverless functions
│   ├── site.py  health.py  cron.py  delivery.py
│   ├── webhook.py  webhook_admin.py  robots.py
│   └── copilot.py              # API Mini App (подпись initData)
├── miniapp/                   # статика Telegram Mini App
├── copilot/                   # ядро Copilot (чистые модули, без Telegram и SQL)
│   ├── fit.py grade_audit.py hr_link.py enrich.py
│   ├── forward.py outcome.py upskill.py metrics.py
│   ├── llm.py security.py webapp.py webapp_auth.py
│   ├── pdf.py store.py credits.py
├── supabase/
│   ├── migrations/            # P7…P12
│   └── functions/             # growth-proxy, interactive-growth-proxy
├── channel_bot.py             # редакционный пайплайн и команды
├── durable_product_state.py   # интерактивный рантайм поверх Postgres
├── interactive_webhook_runtime.py
├── localized_webhook_runtime.py
├── copilot_webhook_runtime.py # Telegram-клей Copilot
├── publication_authority.py   # единственный издатель в канал
├── message_formatter.py  growth_utils.py  resume_matcher.py
├── saved_searches.py  content_magnets.py  resume_documents.py
├── docs/COPIOT.md  SECURITY.md
├── requirements.txt  .env.example.txt  vercel.json
└── README.md  HANDOFF.md  CHANGELOG_v7.0.md  GROWTH_PLAN.md
```

---

## 🔒 Приватность и безопасность

- **Резюме не персистится.** Текст и байты файла живут только в памяти
  запроса; в БД попадает максимум хэш вакансии и `file_id` готового файла.
- **LLM — по явной кнопке**, с санитайзом недоверенного текста, без контактов
  вакансии и без резюме по умолчанию.
- **Подпись Mini App** — единственный источник личности; подделка `initData`
  даёт 401, просроченная подпись отклоняется.
- **Секреты** — только в окружении, в репозитории лежат лишь имена переменных.
- Подробно: [`SECURITY.md`](SECURITY.md).

---

## 🧪 Тесты

```bash
python -m compileall -q .
python -m unittest -v test_growth_utils test_copilot_runtime test_copilot_webapp_api
```

Полный набор CI (allowlist в `.github/workflows/ci.yml`) — офлайн: без сети,
реальных LLM- и Telegram-вызовов.

---

## ⚠️ Troubleshooting

| Симптом | Причина и решение |
|---|---|
| `interactive runtime requires durable growth backend` | нет `GROWTH_DATABASE_URL`/`GROWTH_HTTP_KEY` или `REQUIRE_DURABLE_GROWTH=true` без Postgres |
| `Growth proxy must be the HTTPS Supabase ... endpoint` | `GROWTH_DATABASE_URL` должен указывать на `growth-proxy` или `interactive-growth-proxy` проекта Supabase |
| Вакансии не публикуются | проверить `/api/health`, `/status`, `/sources`; канал пишет только Vercel production |
| Mini App отвечает 401 | истёк `auth_date` (>24 ч) или страница открыта вне Telegram |
| 402 в Mini App | кончились бесплатные кредиты |

---

## 📄 Лицензия

MIT License — см. [MIT.txt](MIT.txt). Шрифт `copilot/fonts/DejaVuSans.ttf` —
DejaVu Fonts License (см. `copilot/fonts/LICENSE_DEJAVU.txt`).

## 🙏 Благодарности

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Telethon](https://github.com/LonamiWebs/Telethon)
- [fpdf2](https://github.com/py-pdf/fpdf2), [trafilatura](https://github.com/adbar/trafilatura)
