# Runbook: релиз v7.0 AI Career Copilot — шаги 2–4

Дата: 2026-10-20. Режим: ручной (шаги выполняет владелец аккаунтов).

## Что уже сделано и проверено

Снимок состояния на 2026-10-20. Значения счётчиков меняются сами, остальные строки — факты о коде.

| Проверка | Результат |
|---|---|
| `main` | все шесть этапов плана (PR #53–#58) + правка валидатора `growth-proxy` (`6a6978b`) + PR #60 (`interactive-growth-proxy`) + PR #61 (runbook) + PR #62 (верификатор) + PR #63 (бейдж грейда и дымовой прогон) |
| PR #53–#63 | все смержены в `main`, CI зелёный на 3.12/3.13 |
| Production Smoke на `main` | `success` |
| `GET /api/health` | 200, `durable_growth: true`, `missing_required: []`, `published_7d: 46` (счётчик растёт) |
| `GET /copilot` | 200, HTML Mini App (2308 байт) |
| `GET /miniapp/app.js` | 200 (8536 байт) |
| `POST /api/copilot` с подделанным `initData` | 401 «Сессия недействительна. Открой приложение заново.» |
| `GET growth-proxy/health` | 200 `{"ok":true,"render_bridge":true}` |
| Локальные тесты | 572 теста / 56 модулей — OK |
| Дымовой прогон ядра | 101 живая вакансия, 0 проблем, детерминированность соблюдена |

Три шага развёртывания: **миграции P9–P12** → **деплой Edge-функций с правкой валидатора** → **флаги Copilot в production + передеплой**.

### Статус развёртывания (проверено из production 2026-10-20)

Все три шага закрыты:

| Шаг | Состояние | Чем подтверждено |
|---|---|---|
| Миграции P9–P12 | **применены** | `copilot.tables: true` — проба из рантайма спрашивает базу через тот же Edge-мост |
| Edge-функции | **задеплоены с правкой валидатора** | `copilot.validator_ok: true` — задеплоенная сборка принимает запрос начисления кредитов |
| Флаги в production | **включены** | `copilot.enabled/pdf/forward/outcome/upskill/webapp: true`, `llm: false`, `webapp_url: true` |

Полная картина — `python tools\verify_release_v7.py`: 14 проверок, 0 `FAIL`, 3 `SKIP` (глубокие проверки, которым нужны `SUPABASE_ACCESS_TOKEN`, `VERCEL_TOKEN` и `GROWTH_BRIDGE_KEY`).

Задеплоенная сборка Edge-функций старше PR #64, поэтому в `/health` ещё нет поля `validator` — функциональная проба приоритетнее маркера, и верификатор это учитывает.

## Как убедиться, что развёртывание завершено — без единого секрета

Три публичных сигнала, по одному на каждый ручной шаг (кроме миграций — их видно только изнутри базы):

| Шаг | Сигнал | Ожидаемое значение | Факт на 2026-10-20 |
|---|---|---|---|
| 1. Миграции | `GET /api/health` → `copilot.tables` | `true` | `true` |
| 2. Edge-функции | `GET /api/health` → `copilot.validator_ok` | `true` | `true` |
| 2. Edge-функции | `GET .../functions/v1/growth-proxy/health` → поле `validator` | `2` | поля нет (сборка старше PR #64, функционально исправна) |
| 3. Флаги | `GET /api/health` → секция `copilot` | `enabled`, `pdf`, `forward`, `outcome`, `upskill`, `webapp` — все `true` | так |

Версия валидатора `2` означает, что задеплоена сборка, понимающая `IS [NOT] DISTINCT FROM` как сравнение значений, а не как ссылку на таблицу. Поле `copilot.tables` — это проверка наличия таблиц P9–P12: Vercel спрашивает базу через тот же Edge-мост с тем же бот-токеном, что и сам рантайм, поэтому ответ «есть/нет» приходит без публикации никаких учётных данных. Значение `null` означает «не удалось определить» (мост настроен иначе или недоступен), а не «нет». Секция `copilot` в health содержит только булевы — ни значения, ни URL, ни секретов.

Быстрая проверка одной командой:

```powershell
curl.exe -s https://junior-middle-it.vercel.app/api/health | Select-String -Pattern 'copilot'
curl.exe -s https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy/health
curl.exe -s https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/interactive-growth-proxy/health
```


## Порядок выполнения

1. **Миграции.** Без таблиц `growth_copilot_*` ни детерминированный разбор, ни кредиты, ни Mini App API не работают: и Edge-мост, и Vercel-бот ходят в Postgres.
2. **Edge-функции.** Правка валидатора обязана попасть в прод до включения флагов, иначе запросы вида `IS DISTINCT FROM CURRENT_DATE` отклоняются с `400 query rejected`.
3. **Флаги.** Переменные окружения в Vercel применяются только к **новым** деплоям, поэтому после `vercel env add` нужен явный редеплой.
4. **Проверка.**

---

# Шаг 1. Миграции P9–P12

Все четыре файла идемпотентны (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, повторяемые `REVOKE`/`ENABLE ROW LEVEL SECURITY`), поэтому повторный прогон безопасен.

| Файл | Что создаёт |
|---|---|
| `20261020120000_p9_copilot_tables.sql` | 7 таблиц: profiles, evaluations, documents, credits, credit_events, grade_audits, enrichments + 6 индексов |
| `20261020130000_p10_copilot_ai_reports.sql` | `growth_copilot_ai_reports` + индекс |
| `20261020140000_p11_copilot_forward_and_applications.sql` | `growth_copilot_forwarded_jobs`, `growth_copilot_applications` + 2 индекса |
| `20261020150000_p12_copilot_rate_limits.sql` | `growth_copilot_rate_limits` |
| `20261021060000_p13_copilot_credit_operations.sql` | реестр операций кредитов `growth_copilot_credit_ops` + индекс; применяется отдельно, см. §1.6 |

## 1.1 Вариант A — Supabase SQL Editor (рекомендуемый)

Открыть **Supabase Dashboard → SQL Editor → New query**. Выполнить **четыре блока по очереди, в этом порядке** (P9 → P10 → P11 → P12). Каждый блок — отдельный запрос.

### Блок 1 — P9

```sql
-- P9 Copilot tables: deterministic fit analysis, grade audit, enrichment cache,
-- generated document cache and credit accounting.
--
-- Every table is a growth_* relation so the Edge growth bridge can serve it,
-- and every table stays backend-only: RLS is enabled and no Data API policy is
-- created, so anon/authenticated keep no DML privileges.
--
-- Privacy invariant: no table stores resume text, resume file names or any
-- other personal document content. Only derived numbers, skill names and
-- vacancy level verdicts are persisted.

CREATE TABLE IF NOT EXISTS public.growth_copilot_profiles (
    user_id BIGINT PRIMARY KEY,
    grade TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'settings',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_evaluations (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    fit_score INTEGER NOT NULL DEFAULT 0 CHECK (fit_score >= 0 AND fit_score <= 100),
    stack_match_score INTEGER NOT NULL DEFAULT 0 CHECK (stack_match_score >= 0 AND stack_match_score <= 100),
    keyword_overlap_pct INTEGER NOT NULL DEFAULT 0 CHECK (keyword_overlap_pct >= 0 AND keyword_overlap_pct <= 100),
    matched_keywords TEXT NOT NULL DEFAULT '[]',
    missing_skills TEXT NOT NULL DEFAULT '[]',
    llm_used BOOLEAN NOT NULL DEFAULT FALSE,
    model TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_documents (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    telegram_file_id TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    fit_score INTEGER NOT NULL DEFAULT 0 CHECK (fit_score >= 0 AND fit_score <= 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_credits (
    user_id BIGINT PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    free_granted_on DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_grade_audits (
    job_hash TEXT PRIMARY KEY,
    declared_grade TEXT NOT NULL DEFAULT '',
    real_grade TEXT NOT NULL DEFAULT '',
    realism_score INTEGER NOT NULL DEFAULT 0 CHECK (realism_score >= 0 AND realism_score <= 100),
    is_fake_grade BOOLEAN NOT NULL DEFAULT FALSE,
    badge_text TEXT NOT NULL DEFAULT '',
    signals TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_enrichments (
    job_hash TEXT PRIMARY KEY,
    source_url TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_evaluations_created
    ON public.growth_copilot_evaluations(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_documents_created
    ON public.growth_copilot_documents(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_credit_events_user
    ON public.growth_copilot_credit_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_credit_events_reason
    ON public.growth_copilot_credit_events(reason, created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_grade_audits_created
    ON public.growth_copilot_grade_audits(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_enrichments_fetched
    ON public.growth_copilot_enrichments(fetched_at);

ALTER TABLE public.growth_copilot_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_credit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_grade_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_enrichments ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_profiles FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_evaluations FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_documents FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_credits FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_credit_events FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_grade_audits FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_enrichments FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.growth_copilot_credit_events_id_seq FROM anon, authenticated;
```

### Блок 2 — P10

```sql
-- P9 Copilot (Stage C): cache of the optional AI analyses.
--
-- The AI layer is only reached through an explicit button, and its answer is
-- cached here so a repeated tap does not pay for a second provider call.
--
-- Privacy invariant: the table never stores resume text, resume file names or
-- any other personal document content. An answer that was produced with the
-- resume is stored without its text, because such an answer may echo personal
-- data back to the user.

CREATE TABLE IF NOT EXISTS public.growth_copilot_ai_reports (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    used_resume BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_ai_reports_created
    ON public.growth_copilot_ai_reports(created_at);

ALTER TABLE public.growth_copilot_ai_reports ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_ai_reports FROM anon, authenticated;
```

### Блок 3 — P11

```sql
-- P9 Copilot (Stage D): forwarded vacancies and the application tracker.
--
-- Forward-to-Analyze lets a user forward a vacancy into the private chat and
-- get the same deterministic analysis as for a channel post. The forwarded
-- payload is stored under the hash of its text, so the same post always maps
-- to one row and the buttons of the analysis card keep working after a
-- redeploy.
--
-- The application tracker remembers that the user sent a reply and which
-- answer came back, so the scheduler can ask about the result exactly once.
--
-- Privacy invariant: the forwarded text is a public vacancy posting - the
-- forward handler refuses resume-like texts before they reach this table, and
-- no column holds resume content, file names or other personal documents.

CREATE TABLE IF NOT EXISTS public.growth_copilot_forwarded_jobs (
    job_hash TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_applications (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'sent',
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reminded_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_forwarded_jobs_created
    ON public.growth_copilot_forwarded_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_applications_pending
    ON public.growth_copilot_applications(stage, sent_at);

ALTER TABLE public.growth_copilot_forwarded_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_applications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_forwarded_jobs FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_applications FROM anon, authenticated;
```

### Блок 4 — P12

```sql
-- P9 Copilot (Stage E): rate limiter of the Mini App API.
--
-- The Mini App authenticates every request with the Telegram signature, but a
-- signed client can still be used too often. The limiter counts the requests
-- of one user inside a fixed window and never stores anything but the counter.

CREATE TABLE IF NOT EXISTS public.growth_copilot_rate_limits (
    user_id BIGINT PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hits INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.growth_copilot_rate_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_rate_limits FROM anon, authenticated;
```

## 1.2 Вариант B — Management API

Нужен personal access token Supabase (**Dashboard → Account → Access Tokens → Generate**). Токен — только в переменную окружения, в репозиторий, коммиты и чат не попадает.

```powershell
cd <корень репозитория>
$env:SUPABASE_ACCESS_TOKEN = "<свой PAT>"
$ref  = "uucjptyqfsdzqsftgiro"
$headers = @{ Authorization = "Bearer $env:SUPABASE_ACCESS_TOKEN"; "Content-Type" = "application/json" }
foreach ($file in @(
  "supabase/migrations/20261020120000_p9_copilot_tables.sql",
  "supabase/migrations/20261020130000_p10_copilot_ai_reports.sql",
  "supabase/migrations/20261020140000_p11_copilot_forward_and_applications.sql",
  "supabase/migrations/20261020150000_p12_copilot_rate_limits.sql"
)) {
  $body = @{ query = (Get-Content $file -Raw -Encoding UTF8) } | ConvertTo-Json -Compress
  try {
    $null = Invoke-RestMethod -Uri "https://api.supabase.com/v1/projects/$ref/database/query" -Method Post -Headers $headers -Body $body
    Write-Output "OK   $file"
  } catch {
    Write-Output "FAIL $file : $($_.Exception.Message)"
  }
}
```

Ожидается четыре строки `OK`. Порядок — как в списке (P9 → P10 → P11 → P12).

Альтернатива — прямое подключение к Postgres (`psql`, см. 1.3).

## 1.3 Вариант C — psql

```bash
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20261020120000_p9_copilot_tables.sql
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20261020130000_p10_copilot_ai_reports.sql
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20261020140000_p11_copilot_forward_and_applications.sql
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20261020150000_p12_copilot_rate_limits.sql
```

`-v ON_ERROR_STOP=1` обязателен: без него psql продолжит после ошибки и покажет ложный «успех».

## 1.4 Проверка после применения (SQL Editor)

```sql
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname LIKE 'growth_copilot_%'
ORDER BY c.relname;
```

Ожидается **11 строк**, у всех `rls_enabled = true`:

`growth_copilot_ai_reports`, `growth_copilot_applications`, `growth_copilot_credit_events`, `growth_copilot_credits`, `growth_copilot_documents`, `growth_copilot_enrichments`, `growth_copilot_evaluations`, `growth_copilot_forwarded_jobs`, `growth_copilot_grade_audits`, `growth_copilot_profiles`, `growth_copilot_rate_limits`.

Проверка прав (не должно быть ни `anon`, ни `authenticated`):

```sql
SELECT table_name, grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND table_name LIKE 'growth_copilot_%'
  AND grantee IN ('anon', 'authenticated')
ORDER BY table_name, grantee;
```

Ожидается **0 строк**.

## 1.5 Чего делать не надо

`supabase db push` здесь **не использовать**: в репозитории нет `supabase/config.toml`, а в базе нет истории `supabase_migrations.schema_migrations`, поэтому `db push` попытается применить весь каталог миграций (включая P13/P14) и может задеть уже работающие таблицы публикации. Только SQL Editor, Management API или psql.

## 1.6 P13/P14 — реестр операций кредитов и AI-claim (отдельная процедура)

P13 защищает от двойного списания и от возврата чужого кредита, поэтому у него два отличия от P9–P12:

1. **DDL можно применить заранее** — таблица `growth_copilot_credit_ops` аддитивна, старый код её не читает.
2. **Перенос состояния требует окна без платных операций** — backfill читает журнал одним снимком, и операция, записанная старым кодом во время переноса, в реестр не попадёт.

Полная процедура с готовыми SQL-блоками, проверками и откатом: **[`docs/P13_CUTOVER.md`](P13_CUTOVER.md)**.

Порядок: выставить оба production-флага в `false` → развернуть **этот** релиз (серверные
гейты есть только в нём) → доказать остановку всех писателей журнала → повторить барьер
исторических ссылок → применить P13 и P14 → выполнить backfill и сверку → включить флаги,
сделать редеплой и провести контрольный E2E обоих транспортов. Без подтверждённого реестра
новый код закрывает платные операции; legacy-fallback удалён. После отказа probe или 42P01
проверка повторяется не чаще раза в 60 секунд и восстанавливает работу без рестарта. Прочие
ошибки реального запроса отклоняют только этот запрос; следующий сразу обращается к БД.
Порядок и риски принятия брошенной оплаты описаны в процедуре cutover.

**Барьер до P13 (шаг 0 процедуры) — обязателен.** Старый код (до PR #70) списывал кредит с
константными ссылками `cv` (Telegram) и `pdf` (Mini App), а новая схема ищет операцию по
`job_hash` — 40-символьному hex-хешу вакансии. Перед P13 выполняется обязательный
SQL-запрос (шаг 0 в `docs/P13_CUTOVER.md`), который находит в журнале ссылки, не
совпадающие с `^[0-9a-f]{40}$`, и отдельно — строки с пустым `ref`. Любая такая строка
останавливает выпуск: сопоставление операции с вакансией и перенос нужно согласовать с
владельцем вручную, автоматически переносить их под неверными идентификаторами нельзя.
Тот же аудит выполняет `tools/verify_release_v7.py` (пункты `supabase.copilot_legacy_refs`
и `supabase.copilot_empty_refs`) и даёт FAIL — в том числе когда подтвердить аудит не
удалось (нет доступа к базе): «не проверил» и «нечего переносить» — не одно и то же.
Сам backfill переносит только `ref ~ '^[0-9a-f]{40}$'`, поэтому строки-сироты не создаются.

**Заморозка (шаг 2 процедуры) — сначала два флага, потом редеплой:**

```powershell
vercel env add COPILOT_ENABLED    production --value false --no-sensitive --force --yes
vercel env add COPILOT_PDF_ENABLED production --value false --no-sensitive --force --yes
vercel redeploy --target production
```

Остановка должна быть **доказана** тремя проверками: `POST /api/copilot/pdf` отвечает 503
`maintenance` (а без подписи — 401), в логах Vercel есть `Copilot paid callback refused:
maintenance`, и `growth_copilot_credit_events` не растёт в течение минуты (drain завершён).
Только после этого применяются P13 и P14.

Изменение переменной окружения **не влияет на уже работающие инстансы** — редеплой
обязателен. Оба писателя кредитов — это два Vercel-приложения (бот и Mini App API), а
Render (`render_main.py`) — только публикационный мост, кредитов он не касается.

**Включение обратно (шаг 6 процедуры) — тоже два флага**, затем редеплой и контролируемый E2E обоих транспортов: платное действие через Mini App API и через Telegram-бота, одна запись `spend` в журнале и строка `delivered` в реестре. Если E2E не прошёл — вернуть флаги в `false`, редеплой, откат деплоя Vercel на предыдущий.

**Проверка схемы:** `tools/verify_release_v7.py` требует и таблицу P13 `growth_copilot_credit_ops`, и колонку P14 `growth_copilot_ai_reports.claim` (тип `text`, `NOT NULL`, default `''`), и пустой аудит исторических ссылок. Без P14 проверка даёт FAIL.

---

# Шаг 2. Edge-функции: `growth-proxy` и `interactive-growth-proxy`

Зачем обе: `growth-proxy` обслуживает Render-публикацию, `interactive-growth-proxy` — Vercel-бот (он ходит в базу под `X-Telegram-Bot-Token`). Правка валидатора нужна в обоих: без неё запрос

```sql
UPDATE growth_copilot_credits ... WHERE free_granted_on IS DISTINCT FROM CURRENT_DATE
```

читается сканом отношений как обращение к таблице `current_date` и отклоняется целиком (`400 query rejected`), то есть суточные бесплатные кредиты не начисляются, и сборка CV в Mini App/боте падает.

`growth-proxy` уже исправлен коммитом `6a6978b`; `interactive-growth-proxy` исправлен PR #60 (тот же приём + регрессионные тесты).

## 2.1 Подготовка

```powershell
supabase --version            # 2.121.0+
supabase login                # откроет браузер; вставьте PAT из Dashboard
supabase projects list        # убедиться, что ref = uucjptyqfsdzqsftgiro
supabase link --project-ref uucjptyqfsdzqsftgiro
```

`supabase link` запросит пароль базы (Database password в Dashboard → Settings → Database). Он не сохраняется в репозитории.

## 2.2 Секреты функции

Функции нужна переменная `SUPABASE_DB_URL` (прямое подключение к Postgres). Секреты уровня проекта переживают деплой, но проверить надо:

```powershell
supabase secrets list
```

Если `SUPABASE_DB_URL` в списке нет — задать (значение не выводить и не коммитить):

```powershell
supabase secrets set SUPABASE_DB_URL="postgresql://postgres.<REF>:<PASSWORD>@db.<REF>.supabase.co:5432/postgres"
```

## 2.3 Деплой

```powershell
cd <корень репозитория>

supabase functions deploy growth-proxy --project-ref uucjptyqfsdzqsftgiro --no-verify-jwt --use-api
supabase functions deploy interactive-growth-proxy --project-ref uucjptyqfsdzqsftgiro --no-verify-jwt --use-api
```

Флаг `--no-verify-jwt` **обязателен**: и Render-мост, и Vercel-бот обращаются к функциям без Supabase JWT (с `X-Growth-Key` / `X-Telegram-Bot-Token`), при включённой проверке JWT они получат 401 и вся публикация и бот упадут. Сейчас проверка уже выключена, поэтому флаг просто сохраняет поведение.

`--use-api` собирает функцию на стороне Supabase — Docker не нужен.

## 2.4 Проверка health

```powershell
curl.exe -s -w "`nHTTP %{http_code}`n" https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy/health
```

Ожидается:

```
HTTP 200
{"ok":true,"service":"growth-proxy","protocol":2,"render_bridge":true}
```

`ok: false` означает, что не задан `SUPABASE_DB_URL` → вернуться к п. 2.2.

## 2.5 Проверка правки валидатора

Через Render-мост (ключ `GROWTH_BRIDGE_KEY` лежит в env сервиса Render; в чат и репозиторий его не выносить):

```powershell
$body = '{"query":"SELECT balance FROM growth_copilot_credits WHERE user_id = 1 AND free_granted_on IS DISTINCT FROM CURRENT_DATE","params":[]}'
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy `
  -H "x-growth-key: $env:GROWTH_BRIDGE_KEY" -H "Content-Type: application/json" -d $body
```

Ожидается **HTTP 200** с `{"rows":[],"columns":["balance"],"rowcount":0}`.
Без правки валидатора здесь был бы **HTTP 400** `{"error":"query rejected"}`.

Для `interactive-growth-proxy` заголовок другой — `x-telegram-bot-token` с токеном именно `junior_jobs_channel_bot`:

```powershell
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/interactive-growth-proxy `
  -H "x-telegram-bot-token: $env:TELEGRAM_BOT_TOKEN" -H "Content-Type: application/json" -d $body
```

Тот же ожидаемый результат: HTTP 200.

Если токена под рукой нет, проверку можно сделать через продукт: собрать CV в боте — доставка PDF означает, что начисление кредита прошло.

---

# Шаг 3. Флаги Copilot в production (Vercel)

**Флаги ставятся в Vercel, а не в Render.** Интерактивный бот живёт на Vercel (`/api/webhook`), Mini App API — там же (`/api/copilot`); long polling на Render выключен (нет `TELEGRAM_BOT_TOKEN`, fail-closed).

| Переменная | Значение | Зачем |
|---|---|---|
| `COPILOT_ENABLED` | `true` | Базовый переключатель: карточка разбора, `/copilot`, кнопки |
| `COPILOT_PDF_ENABLED` | `true` | Сборка CV под вакансию (1 кредит) |
| `COPILOT_FORWARD_ENABLED` | `true` | Разбор пересланной вакансии |
| `COPILOT_OUTCOME_ENABLED` | `true` | Трекер откликов и напоминание через 7 дней |
| `COPILOT_UPSKILL_ENABLED` | `true` | Радар `/upskill` |
| `COPILOT_WEBAPP_ENABLED` | `true` | Кнопка «🖥 Открыть Mini App» в карточке разбора |
| `COPILOT_WEBAPP_URL` | `https://junior-middle-it.vercel.app/copilot` | Публичный URL Mini App |
| `COPILOT_LLM_ENABLED` | `false` | Ключа провайдера нет — слой LLM выключен, кнопка AI не показывается |
| `COPILOT_LLM_SEND_RESUME` | `false` | Резюме в модель не уходит (инвариант приватности) |
| `COPILOT_ENRICH_ENABLED` | `false` | Догрузка полного текста вакансии извне — на запуске выключена |

Значения по умолчанию, которые трогать не нужно: `COPILOT_FREE_PER_DAY=3`, `COPILOT_REFERRAL_BONUS=5`, `COPILOT_FOLLOWUP_DAYS=7`, `COPILOT_UPSKILL_DAYS=30`.

## 3.1 Команды

```powershell
cd <корень репозитория>
vercel login
vercel link --project junior-middle-it --scope olegs-projects-bfc4e11a

vercel env add COPILOT_ENABLED      production --value true  --no-sensitive --force --yes
vercel env add COPILOT_PDF_ENABLED   production --value true  --no-sensitive --force --yes
vercel env add COPILOT_FORWARD_ENABLED production --value true --no-sensitive --force --yes
vercel env add COPILOT_OUTCOME_ENABLED production --value true --no-sensitive --force --yes
vercel env add COPILOT_UPSKILL_ENABLED production --value true --no-sensitive --force --yes
vercel env add COPILOT_WEBAPP_ENABLED production --value true  --no-sensitive --force --yes
vercel env add COPILOT_WEBAPP_URL    production --value "https://junior-middle-it.vercel.app/copilot" --no-sensitive --force --yes
vercel env add COPILOT_LLM_ENABLED   production --value false --no-sensitive --force --yes
vercel env add COPILOT_LLM_SEND_RESUME production --value false --no-sensitive --force --yes
```

`--no-sensitive` — чтобы значения было видно в `vercel env ls` для проверки (для production Vercel по умолчанию помечает переменные sensitive, и значение потом не читается). Секретов среди этих переменных нет. `--force` — чтобы команда не падала, если переменная уже существует.

Проверить:

```powershell
vercel env ls production
```

Ожидаются все семь `COPILOT_*` со значениями `true` (кроме `COPILOT_LLM_ENABLED=false`, `COPILOT_LLM_SEND_RESUME=false`) и `COPILOT_WEBAPP_URL` с указанным URL.

## 3.2 Передеплой

Изменение env **не запускает** сборку само. Нужен новый деплой:

```powershell
vercel redeploy --target production
```

или из дашборда: **Deployments → ⋯ на текущем production-деплое → Redeploy** (этот вариант сохраняет исходные настройки сборки).

Альтернатива — собрать из локального клона (клон на коммите `main`):

```powershell
vercel --prod
```

## 3.3 Проверка после передеплоя

```powershell
curl.exe -s -w "`nHTTP %{http_code}`n" https://junior-middle-it.vercel.app/api/health
curl.exe -s -o NUL -w "HTTP %{http_code}`n" https://junior-middle-it.vercel.app/copilot
curl.exe -s -o NUL -w "HTTP %{http_code}`n" https://junior-middle-it.vercel.app/miniapp/app.js
curl.exe -s -w "`nHTTP %{http_code}`n" -X POST https://junior-middle-it.vercel.app/api/copilot -H "Content-Type: application/json" -d "{\"init_data\":\"forged\"}"
```

Ожидания: `/api/health` → 200 и `missing_required: []`; `/copilot` → 200; `/miniapp/app.js` → 200; `/api/copilot` с подделанной подписью → **401** («Сессия недействительна. Открой приложение заново.»).

---

# Шаг 4. End-to-end чек-лист в Telegram

## 4.0 Автоматическая проверка одной командой

В репозитории лежит готовый верификатор релиза — он проверяет все поверхности и печатает матрицу `OK / FAIL / SKIP`:

```powershell
cd <корень репозитория>
python tools\verify_release_v7.py
```

Без секретов проверяются: контракт `/api/health`, доставка Mini App, гейт подписи `initData` и обе Edge-функции. Проверки миграций, флагов Vercel и фикса валидатора требуют `SUPABASE_ACCESS_TOKEN`, `VERCEL_TOKEN` и `GROWTH_BRIDGE_KEY` соответственно — без них они помечаются как `SKIP`, а не как ошибка:

```powershell
$env:SUPABASE_ACCESS_TOKEN = "<PAT Supabase>"   # проверка таблиц P9-P12
$env:VERCEL_TOKEN         = "<токен Vercel>"     # проверка флагов production
$env:GROWTH_BRIDGE_KEY    = "<ключ Render-моста>"# проверка правки валидатора
python tools\verify_release_v7.py
```

Ожидаемый результат после всех трёх шагов — **ноль `FAIL` и ноль `SKIP`**. До этого момента верификатор честно показывает незакрытые шаги: `prod.copilot_rollout` — флаги ещё выключены, `edge.*.validator` — Edge-функции ещё не передеплоены. Машиночитаемый вывод: `python tools\verify_release_v7.py --json`.

Детерминированное ядро Copilot можно проверить и вообще без секретов — на живых вакансиях из публичной проекции Edge:

```powershell
python tools\copilot_core_smoke.py --limit 20
```

Скрипт прогоняет настоящие модули (`copilot.fit`, `copilot.grade_audit`, `copilot.hr_link`) по реальным payload'ам и печатает fit-score для трёх профилей, вердикт по грейду с бейджем и найденные контакты. Ненулевой `problems` означает, что ядро где-то падает, выдаёт значение вне диапазона или перестаёт быть детерминированным.

## 4.1 Ручной чек-лист в Telegram

1. Открыть **@junior_jobs_channel_bot**, отправить `/copilot` — приходит карточка разбора с fit-score и кнопками.
2. В карточке есть кнопка **«🖥 Открыть Mini App»** — она появляется только при `COPILOT_WEBAPP_ENABLED=true` и заданном `COPILOT_WEBAPP_URL`. BotFather и `setChatMenuButton` не нужны: кнопка inline с `web_app` (direct link), Telegram подписывает `initData` токеном бота автоматически.
3. Mini App открывается, показывает вкладки Fit / Резюме / Поиск.
4. «Собрать CV» в Mini App — PDF приходит в чат, кредит списан. Если приходит «Бесплатные сборки на сегодня закончились» или «Сервис временно недоступен», вернуться к п. 2.5 (начисление кредита идёт через Edge-мост).
5. Переслать вакансию в личку — приходит тот же разбор (нужен `COPILOT_FORWARD_ENABLED=true`).
6. `/upskill` — приходит радар (нужен `COPILOT_UPSKILL_ENABLED=true`).
7. Кнопка «Отклик отправлен» — трекер откликов (нужен `COPILOT_OUTCOME_ENABLED=true`).
8. Кнопка AI **не показывается** — так и должно быть при `COPILOT_LLM_ENABLED=false`.

---

# Шаг 5. Откат

| Что | Как |
|---|---|
| Отключить Copilot | `vercel env add COPILOT_ENABLED production --value false --no-sensitive --force --yes` + `vercel redeploy --target production` |
| Откатить флаги | то же для каждой переменной; Mini App и бот продолжают работать в обычном режиме |
| Вернуть Edge-функцию | `supabase functions deploy growth-proxy --project-ref uucjptyqfsdzqsftgiro --no-verify-jwt --use-api` на предыдущем коммите (`git checkout 053608ee` в клоне) |
| Откатить код Vercel | дашборд → Deployments → ⋯ → Promote to Production на деплое `053608ee`/`6a6978b` |
| Откатить релиз с P13/P14 | сначала Promote to Production на деплое **до PR #70**, и только потом (если нужно) сносить таблицу реестра |

DDL откатывается вручную: `DROP TABLE public.growth_copilot_*` — только если нужно, данные там производные и восстанавливаются. Таблицу реестра P13 (`growth_copilot_credit_ops`) можно сносить **только после** отката деплоя: новый код без реестра закрывает платные операции, а откат на код до PR #70 возвращает прежние гонки двойного списания. Полная процедура отката по этапам — в `docs/P13_CUTOVER.md`, §3.

---

# Приложение: справочные значения

| Параметр | Значение |
|---|---|
| Supabase project ref | `uucjptyqfsdzqsftgiro` |
| Edge base URL | `https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/` |
| Прод Vercel | `https://junior-middle-it.vercel.app` |
| Mini App URL | `https://junior-middle-it.vercel.app/copilot` |
| Vercel project / scope | `junior-middle-it` / `olegs-projects-bfc4e11a` |
| Бот | `@junior_jobs_channel_bot` |
| Репозиторий | `timoshinoleg-eng/junior_middle_it`, ветка `main`, `git clone` + `git checkout main` |
| Команда Edge | `supabase functions deploy <name> --project-ref uucjptyqfsdzqsftgiro --no-verify-jwt --use-api` |

Секреты (PAT Supabase, VERCEL_TOKEN, пароль БД, токен бота, `GROWTH_BRIDGE_KEY`) в репозиторий, коммиты, логи и чат не попадают.
