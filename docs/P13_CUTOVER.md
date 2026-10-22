# P13/P14 — применение реестра операций и отдельного AI-claim (junior_middle_it)

Документ для **вашей** подготовки и применения в проде. Ничего из этого я не применял сам:
ни миграции, ни деплоя, ни merge. Все команды написаны так, чтобы вы могли выполнить их
одну за другой и проверить результат на каждом шаге.

---

## 0. Зачем это нужно (коротко)

PR #70 (коммит `98313ae`) закрывает гонку, из-за которой **два параллельных запроса по одной
вакансии могли списать у пользователя два кредита**, а чужой расчёт мог **вернуть кредит при
неоднозначной доставке** (нарушение fail-closed).

Защита требует таблицу `public.growth_copilot_credit_ops` (P13) и колонку
`public.growth_copilot_ai_reports.claim` (P14). Пока реестр не подтверждён, новые
платные операции закрыты: пользователь получает сообщение о невозможности проверить кредиты.
Fallback по журналу удалён. Откат продукта — откат деплоя, а не включение legacy-режима.

Прямой путь проверяет наличие реестра SQL-запросом с отношением; Edge-мост принимает
тот же запрос. SQLSTATE 42P01 означает отсутствие P13; HTTP 400/503 означает, что
наличие реестра через мост не подтверждено (это не доказательство отсутствия таблицы).
После отказа probe или SQLSTATE 42P01 повторная проверка выполняется не чаще раза в 60 секунд; успешная
проверка восстанавливает работу без рестарта. Временная ошибка реального запроса
(сеть, HTTP 400/503) или ошибка параметров отклоняет только текущий запрос, не
блокируя реестр на минуту; следующий запрос сразу обращается к БД. Ошибка P14 также закрывает операции,
использующие AI-claim, поэтому обе миграции нужны до включения нового кода.

Порядок: выставить оба production-флага в `false` → развернуть **этот** релиз (он приносит
серверные гейты) → доказать, что все писатели журнала остановились → повторить барьер
исторических ссылок → применить P13 и P14 → выполнить backfill и сверку → включить флаги,
сделать редеплой и проверить оба транспорта. Ни миграции, ни деплой этот документ не
выполняет.

Обоснование порядка «деплой раньше миграции» — в §1: серверные гейты есть только в этом
релизе, и без них заморозка держалась бы исключительно на флагах, которые старый код в
платных путях не проверяет.

---

## 1. Окно работ и заморозка платных операций

| | |
|---|---|
| Длительность окна | 20–30 минут |
| Что нужно остановить | Все источники: Telegram PDF и AI, Mini App, webhook-обработчики и фоновые повторы |
| Порядок | Флаги в `false` → **деплой этого релиза** (в нём серверные гейты) → доказательства остановки → **барьер legacy-ссылок** → P13/P14 и backfill → флаги в `true` + редеплой → E2E обоих транспортов |
| Что можно делать параллельно | Всё, что не пишет кредиты: публикация вакансий, просмотр вакансий, рефералы |

**Заморозка — два флага.**

```powershell
vercel env add COPILOT_ENABLED    production --value false --no-sensitive --force --yes
vercel env add COPILOT_PDF_ENABLED production --value false --no-sensitive --force --yes
```

Выключать только `COPILOT_ENABLED` недостаточно: сборка CV включается отдельным флагом
`COPILOT_PDF_ENABLED`.

**Изменение переменной окружения не влияет на уже работающие инстансы.** Флаг читается
процессом при старте, поэтому после `vercel env add` платные операции продолжают работать
до тех пор, пока не поднимутся новые инстансы. Отсюда следующий шаг — обязательный
редеплой:

```powershell
vercel redeploy --target production
```

**Почему деплой идёт до миграции, а не после.** Серверные гейты (503 `maintenance` на
`POST /api/copilot/pdf`, отказ старых Telegram-кнопок, остановка подвешенной загрузки
резюме) есть **только в этом релизе**. На старом деплое заморозка держится исключительно на
флагах, а старый код флагов в платных путях не проверяет вовсе: прямые запросы Mini App и
ранее отправленные кнопки продолжили бы списывать кредиты во время backfill. Поэтому
порядок жёсткий: флаги `false` → деплой этого релиза → доказательства остановки →
барьер legacy-ссылок → P13/P14 → backfill. Обратный порядок (миграция → деплой) оставляет
окно, в котором старый код продолжает писать в журнал, а backfill читает его без учёта.

**Доказать, что писатели остановились.** Только после всех трёх проверок можно применять
P13/P14:

1. **Mini App API закрыт.** `POST /api/copilot/pdf` (с валидным `initData`) отвечает
   `503` с `{"ok": false, "error": "maintenance"}`. Без валидного `initData` ожидается
   `401` — проверка подписи остаётся первой и при maintenance.
2. **Бот отказывает.** В логах Vercel есть `Copilot paid callback refused: maintenance`
   (или `Copilot PDF refused: maintenance` / `Copilot AI refused: maintenance`) после
   нажатия старой кнопки разбора.
3. **Журнал перестал расти.** Счётчик и метка времени не меняются в течение минуты — все
   писатели остановились, начатые сборки и вызовы модели завершились (drain).

```sql
SELECT count(*) AS journal_rows, max(created_at) AS last_event
FROM public.growth_copilot_credit_events;
```

Проверку 3 повторяют дважды с интервалом в минуту. Если журнал растёт — drain не завершён,
P13/P14 не применять.

### Фактическая топология

| Компонент | Где живёт | Пишет кредиты | Что делать в окне |
|---|---|---|---|
| Интерактивный Telegram-бот | Vercel, `/api/webhook` → `api/webhook.py` → `copilot_webhook_runtime.py` | **да** (PDF, AI-разбор) | флаги + редеплой этого релиза |
| Mini App API | Vercel, `/api/copilot` → `api/copilot.py` | **да** (сборка CV) | тот же редеплой |
| Публикационный мост | Render, `render_main.py` | нет | **перезапуск не нужен**, кредиты не касается |
| Long polling на Render | Render | нет (в проде выключен) | убедиться, что на Render нет `TELEGRAM_BOT_TOKEN` |

Оба писателя кредитов — это два Vercel-приложения, поэтому одного редеплоя с флагами
достаточно, если Render действительно работает без `TELEGRAM_BOT_TOKEN`. Это стоит
проверить глазами в настройках Render-сервиса: если токен на Render есть, polling может
подняться и писать в журнал мимо заморозки — тогда на время окна Render-сервис надо
остановить.

Оба редеплоя (заморозка и включение обратно) поднимают **новые** инстансы Vercel: старые
процессы не перечитывают переменные окружения на лету. Поэтому «включил флаг — и поехали»
не работает, а рестарт инстансов после смены флагов обязателен.



**Почему окно нужно.** Backfill читает журнал одним снимком (`READ COMMITTED`). Если в этот
момент старый код записывает новую резервацию в журнал, её в реестре не будет — а после
деплоя новый код посчитает, что операция не оплачена, и спишет кредит **второй раз**.

Обязательное условие — остановить **все** источники платных операций и дождаться
завершения выполняющихся обработчиков до backfill. Оставить трафик включённым и
«починить журнал после деплоя» небезопасно: первый повтор может списать кредит раньше repair.
Публикацию вакансий и прочие независимые функции останавливать не требуется.

P14 очищает старые токены из `model`, не переносит их в `claim`. Поэтому во время P14
не должно быть живых обработчиков старой версии; иначе их владение потеряется.

---

## 2. Порядок (обязательный)

### Шаг 0. Барьер: в журнале нет исторических платных операций

**Обязательный шаг до P13.** Старый код (до PR #70) списывал кредит с **константными**
ссылками: `"cv"` в Telegram-пути и `"pdf"` в Mini App-пути. Новая схема ищет операцию по
`job_hash` (40-символьный hex). Если такие строки есть, их **нельзя** переносить в реестр
автоматически: получились бы строки-сироты, которые никогда не совпадут с новым запросом,
и уже оплаченная, но не доставленная операция после cutover была бы оплачена повторно.

```sql
-- 1. Платные операции без вакансии (legacy ref: 'cv', 'pdf' или что-то иное)
SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE reason IN ('spend', 'spend_release', 'cv_delivered', 'cv_delivery_unknown', 'llm_call', 'llm_call_release', 'llm_delivered', 'llm_delivery_unknown')
  AND ref IS DISTINCT FROM ''
  AND ref !~ '^[0-9a-f]{40}$'
GROUP BY ref, reason
ORDER BY events DESC;

-- 2. Отдельно: платные операции вообще без ссылки (ref = '' или NULL)
SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE reason IN ('spend', 'spend_release', 'cv_delivered', 'cv_delivery_unknown', 'llm_call', 'llm_call_release', 'llm_delivered', 'llm_delivery_unknown')
  AND (ref = '' OR ref IS NULL)
GROUP BY ref, reason
ORDER BY events DESC;
```

**Аудит смотрит только на платные операции.** Фильтр `reason IN (...)` — обязательная часть
запроса, а не украшение:

- `daily_grant` и `referral` — это **бесплатные начисления**. Они пишутся с пустой ссылкой
  намеренно (`copilot/credits.py`: `grant_daily_allowance_with_event(..., "daily_grant", "")`
  и `grant_credits_with_event(..., "referral", "")`), потому что начисление не относится ни к
  какой вакансии. Их нечего сопоставлять с `job_hash`, и они **не должны** блокировать выпуск.
- `llm_attempt` — учёт вызовов модели для глобального бюджета. Кредит пользователя он не
  списывает (`delta = 0`), к вакансии не привязан и переносом в реестр не занимается.
- Платные причины — `spend`, `spend_release`, `cv_delivered`, `cv_delivery_unknown`,
  `llm_call`, `llm_call_release`, `llm_delivered`, `llm_delivery_unknown` — вот единственные,
  которые сопоставляются с вакансией.

Скобки вокруг `ref = '' OR ref IS NULL` обязательны: без них приоритет оператора неявный, и
запрос молча меняет смысл при правке условий.

Пустой результат **обоих** запросов — единственный допустимый. **Если строки есть, выпуск
останавливается**: сопоставление таких операций с вакансиями и их перенос нужно
согласовать отдельно с владельцем (вручную, по данным кеша документов), автоматически
переносить их под неверными идентификаторами нельзя.

Тот же запрос выполняет `tools/verify_release_v7.py` (пункты `supabase.copilot_legacy_refs`
и `supabase.copilot_empty_refs`): он даёт **FAIL**, если такие строки появились, и **FAIL**,
если аудит не удалось подтвердить (нет credentials, ошибка запроса).

Даже если барьер по какой-то причине пропущен, сам backfill переносит в реестр **только**
`ref`, совпадающий с `^[0-9a-f]{40}$` (условие `ref ~ '^[0-9a-f]{40}$'` в
`supabase/migrations/20261021060000_p13_copilot_credit_operations.sql`). Не-хеш ссылки не
создают строки-сироты, но и не переносят оплату — поэтому пропуск барьера всё равно
приводит к повторному списанию, и барьер обязателен.

### Шаг 1. Снимок состояния «до»

```sql
-- 1.1 Есть ли уже таблица (если NULL — миграция ещё не применялась)
SELECT to_regclass('public.growth_copilot_credit_ops') AS table_exists;

-- 1.2 Сколько событий в журнале (это и есть «истина» по кредитам)
SELECT count(*) AS journal_events FROM public.growth_copilot_credit_events;

-- 1.3 Распределение причин (для понимания, что именно переносится в реестр)
SELECT reason, count(*) FROM public.growth_copilot_credit_events GROUP BY 1 ORDER BY 2 DESC;

-- 1.4 Открытые резервации ( spends > releases ) — именно они станут строками реестра
-- (только 40-символьные хеши: не-хеш ссылки не переносятся, см. шаг 0)
WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
)
SELECT s.user_id, s.ref, s.kind, s.c AS spends, COALESCE(r.c, 0) AS releases
FROM spends s
LEFT JOIN releases r ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
WHERE s.c > COALESCE(r.c, 0)
ORDER BY s.user_id, s.ref;
```

Сохраните вывод 1.4 — он понадобится на шаге 5 для сверки.

### Шаг 2. Заморозка и деплой этого релиза с серверными гейтами

Порядок обязателен: **сначала флаги, потом деплой**. Флаги выключают платные операции на
новых инстансах, а деплой приносит сами гейты — без них старый код продолжил бы
списывать кредиты во время backfill.

```powershell
# 1. Обе переменные в false. Это НЕ действует на уже работающие инстансы.
vercel env add COPILOT_ENABLED    production --value false --no-sensitive --force --yes
vercel env add COPILOT_PDF_ENABLED production --value false --no-sensitive --force --yes

# 2. Новые инстансы: с флагами false и с серверными гейтами этого релиза.
vercel redeploy --target production
```

Что происходит:

- новые инстансы поднимаются с `COPILOT_ENABLED=false` и `COPILOT_PDF_ENABLED=false` →
  платные точки закрыты **на сервере** (`503 maintenance` на `POST /api/copilot/pdf`, отказ
  старых Telegram-кнопок, остановка подвешенной загрузки резюме);
- старые инстансы вытесняются, и их незавершённые обработчики заканчиваются — нужен
  **drain**: дайте им завершиться (обычно секунды, ориентир — 3 минуты).

Затем — **доказательства остановки** из §1 (все три обязательны):

1. `POST /api/copilot/pdf` (с валидным `initData`) → `503` `maintenance`; без подписи → `401`.
2. В логах Vercel есть `Copilot paid callback refused: maintenance` после нажатия старой кнопки.
3. Журнал `growth_copilot_credit_events` не растёт в течение минуты (два замера с
   интервалом в минуту).

**И повторите барьер шага 0.** Обмен инстансов мог оборвать уже начатую платную операцию:
в журнале осталась бы трата со старой ссылкой (`cv`/`pdf`) без расчёта. Такой журнал —
стоп-сигнал: сначала согласование сопоставления, потом P13.

### Шаг 3. DDL: P13 — таблица реестра (можно применить заранее, в любой момент)

Таблица аддитивная: старый код её не читает и не пишет, поэтому шаг безопасен и вне окна.

```sql
CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_ops (
    user_id BIGINT NOT NULL,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'released', 'delivered', 'unknown')),
    amount INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, ref, kind)
);

CREATE INDEX IF NOT EXISTS growth_copilot_credit_ops_updated_at_idx
    ON public.growth_copilot_credit_ops (updated_at);

ALTER TABLE public.growth_copilot_credit_ops ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_credit_ops FROM anon, authenticated;
```

### Шаг 3.1. P14 — отдельная колонка AI-claim (в том же окне)

После P13 применить файл `supabase/migrations/20261021070000_p14_copilot_ai_report_claim.sql`.
Не применять весь каталог миграций через `db push`. P14 идемпотентна:

```sql
-- Keep ownership separate from model metadata.
ALTER TABLE public.growth_copilot_ai_reports
    ADD COLUMN IF NOT EXISTS claim TEXT NOT NULL DEFAULT '';

-- Legacy tokens are not model names; old workers must already be drained.
UPDATE public.growth_copilot_ai_reports SET model = '' WHERE model LIKE 'claim:%';

SELECT column_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'growth_copilot_ai_reports'
  AND column_name = 'claim';
```

Проверить: `claim` существует, `NOT NULL`, значение по умолчанию — пустая строка.
Имя модели остаётся в `model`; ответы с резюме не сохраняются. Сохранение отчёта
не меняет claim: обработчик отдельно освобождает его после завершения, включая пустой ответ.

### Шаг 4. Backfill (только в окне)

#### 4.1 Основной backfill

```sql
BEGIN;

-- Блокировка на время переноса: две одновременные сессии не будут мешать друг другу.
SELECT pg_advisory_xact_lock(20261021);

WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS spends
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS releases
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
), states AS (
    SELECT s.user_id, s.ref, s.kind, s.spends,
           CASE
               WHEN EXISTS (
                   SELECT 1 FROM public.growth_copilot_credit_events d
                   WHERE d.user_id = s.user_id AND d.ref = s.ref
                     AND d.reason = CASE s.kind WHEN 'spend' THEN 'cv_delivered' ELSE 'llm_delivered' END
               ) THEN 'delivered'
               WHEN EXISTS (
                   SELECT 1 FROM public.growth_copilot_credit_events u
                   WHERE u.user_id = s.user_id AND u.ref = s.ref
                     AND u.reason = CASE s.kind WHEN 'spend' THEN 'cv_delivery_unknown' ELSE 'llm_delivery_unknown' END
               ) THEN 'unknown'
               WHEN s.spends > COALESCE(r.releases, 0) THEN 'reserved'
               ELSE 'released'
           END AS state
    FROM spends s
    LEFT JOIN releases r ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
)
INSERT INTO public.growth_copilot_credit_ops
    (user_id, ref, kind, state, amount, attempt, created_at, updated_at)
SELECT user_id, ref, kind, state, 1, spends, NOW(), NOW()
FROM states
WHERE state <> 'released'
  AND ref ~ '^[0-9a-f]{40}$'
ON CONFLICT (user_id, ref, kind) DO NOTHING;

COMMIT;
```

#### 4.2 Repair (если backfill уже выполнялся, а в журнал дописались новые операции)

**Только до деплоя нового кода.** После деплоя источником истины становится реестр, и repair
по журналу может испортить его.

```sql
BEGIN;
SELECT pg_advisory_xact_lock(20261021);

WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS spends
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS releases
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
      AND ref ~ '^[0-9a-f]{40}$'
    GROUP BY 1, 2, 3
), states AS (
    SELECT s.user_id, s.ref, s.kind, s.spends,
           CASE
               WHEN EXISTS (SELECT 1 FROM public.growth_copilot_credit_events d
                            WHERE d.user_id = s.user_id AND d.ref = s.ref
                              AND d.reason = CASE s.kind WHEN 'spend' THEN 'cv_delivered' ELSE 'llm_delivered' END)
                   THEN 'delivered'
               WHEN EXISTS (SELECT 1 FROM public.growth_copilot_credit_events u
                            WHERE u.user_id = s.user_id AND u.ref = s.ref
                              AND u.reason = CASE s.kind WHEN 'spend' THEN 'cv_delivery_unknown' ELSE 'llm_delivery_unknown' END)
                   THEN 'unknown'
               WHEN s.spends > COALESCE(r.releases, 0) THEN 'reserved'
               ELSE 'released'
           END AS state
    FROM spends s
    LEFT JOIN releases r ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
)
INSERT INTO public.growth_copilot_credit_ops
    (user_id, ref, kind, state, amount, attempt, created_at, updated_at)
SELECT user_id, ref, kind, state, 1, spends, NOW(), NOW()
FROM states
WHERE state <> 'released'
  AND ref ~ '^[0-9a-f]{40}$'
ON CONFLICT (user_id, ref, kind) DO UPDATE
    SET state = EXCLUDED.state,
        attempt = EXCLUDED.attempt,
        updated_at = NOW()
WHERE public.growth_copilot_credit_ops.state <> EXCLUDED.state;

COMMIT;
```

### Шаг 5. Сверка (обязательно — не пропускайте)

```sql
-- V1. Таблица на месте, RLS включён
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'growth_copilot_credit_ops';

-- V2. Открытые резервации журнала без строки в реестре → ожидается 0 строк
WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
      AND ref ~ '^[0-9a-f]{40}$' GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
      AND ref ~ '^[0-9a-f]{40}$' GROUP BY 1, 2, 3
)
SELECT s.user_id, s.ref, s.kind, s.c AS spends, COALESCE(r.c, 0) AS releases
FROM spends s
LEFT JOIN releases r ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
WHERE s.c > COALESCE(r.c, 0)
  AND NOT EXISTS (SELECT 1 FROM public.growth_copilot_credit_ops o
                  WHERE o.user_id = s.user_id AND o.ref = s.ref AND o.kind = s.kind);

-- V3. Строки реестра без записи о трате в журнале → ожидается 0 строк
SELECT o.user_id, o.ref, o.kind, o.state
FROM public.growth_copilot_credit_ops o
WHERE NOT EXISTS (SELECT 1 FROM public.growth_copilot_credit_events e
                  WHERE e.user_id = o.user_id AND e.ref = o.ref
                    AND e.reason IN ('spend', 'llm_call'));

-- V4. Распределение состояний (для глаза)
SELECT state, count(*) FROM public.growth_copilot_credit_ops GROUP BY 1 ORDER BY 2 DESC;

-- V5. Баланс пользователя против журнала → ожидается 0 строк
-- (бонусы за рефералы и дневные начисления тоже лежат в журнале, поэтому суммы должны совпадать)
SELECT u.user_id, u.balance, COALESCE(e.delta_sum, 0) AS journal_sum
FROM public.growth_copilot_credits u
LEFT JOIN (SELECT user_id, SUM(delta) AS delta_sum
           FROM public.growth_copilot_credit_events GROUP BY 1) e USING (user_id)
WHERE u.balance <> COALESCE(e.delta_sum, 0);

-- V6. В реестре нет не-хеш ссылок (строк-сирот) → ожидается 0 строк
SELECT o.user_id, o.ref, o.kind, o.state
FROM public.growth_copilot_credit_ops o
WHERE o.ref !~ '^[0-9a-f]{40}$';

-- V7. Барьер шага 0 всё ещё пуст (журнал не изменился после backfill).
-- Те же два запроса, что и в шаге 0, — по тем же платным причинам.
SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE reason IN ('spend', 'spend_release', 'cv_delivered', 'cv_delivery_unknown', 'llm_call', 'llm_call_release', 'llm_delivered', 'llm_delivery_unknown')
  AND ref IS DISTINCT FROM ''
  AND ref !~ '^[0-9a-f]{40}$'
GROUP BY ref, reason
ORDER BY events DESC;

SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE reason IN ('spend', 'spend_release', 'cv_delivered', 'cv_delivery_unknown', 'llm_call', 'llm_call_release', 'llm_delivered', 'llm_delivery_unknown')
  AND (ref = '' OR ref IS NULL)
GROUP BY ref, reason
ORDER BY events DESC;
```

Если V2, V3, V5, V6 или V7 вернули строки — **не продолжайте**. Сначала разберитесь (чаще
всего это значит, что в окно шла платная операция и нужен repair из шага 4.2).

### Шаг 6. Включение флагов, redeploy и контрольные E2E

Код этого релиза уже в проде с шага 2 (он закрыт гейтами), миграции применены, сверка
прошла. Теперь открываем платные операции:

```powershell
vercel env add COPILOT_ENABLED    production --value true --no-sensitive --force --yes
vercel env add COPILOT_PDF_ENABLED production --value true --no-sensitive --force --yes
vercel redeploy --target production
```

Редеплой обязателен: переменные окружения читаются при старте процесса, уже работающие
инстансы их не перечитывают. Новые инстансы поднимаются уже с `true` — иначе гейты
продолжат закрывать платные операции.

**Контролируемый E2E обоих транспортов** (по одному платному действию на каждом, на
тестовом аккаунте):

1. **Mini App API.** `POST /api/copilot/pdf` с валидном `initData` → `200`, документ
   отправлен в чат бота. `POST /api/copilot` без подписи → `401` (подпись проверяется
   первой). Баланс тестового аккаунта уменьшился ровно на 1.
2. **Telegram-бот.** Кнопка разбора/сборки CV → документ приходит в чат, кредит списан
   один раз.
3. **Реестр.** Для тестовой операции появилась строка со `state = 'delivered'`, а в журнале
   — одна запись `spend`:

```sql
SELECT user_id, ref, kind, state, attempt, updated_at
FROM public.growth_copilot_credit_ops
ORDER BY updated_at DESC LIMIT 5;

SELECT reason, count(*) FROM public.growth_copilot_credit_events
WHERE created_at > now() - interval '10 minutes' GROUP BY 1;

-- Нет расхождений баланса
SELECT u.user_id, u.balance, COALESCE(e.delta_sum, 0) AS journal_sum
FROM public.growth_copilot_credits u
LEFT JOIN (SELECT user_id, SUM(delta) AS delta_sum
           FROM public.growth_copilot_credit_events GROUP BY 1) e USING (user_id)
WHERE u.balance <> COALESCE(e.delta_sum, 0);
```

**В логах после проверки обоих транспортов** ожидается
`Credit operation registry confirmed; attempt protection enabled`.
`migration P13 is not applied` означает отсутствие P13 на прямом пути;
`Credit registry not confirmed through bridge/backend` с HTTP 400/503 требует
проверки схемы, SQL-валидатора и доступности моста. Платные операции в это время
закрыты для текущего запроса. Глобальная пауза до 60 секунд включается только при
42P01 или отказе probe; прочие ошибки реального запроса не задерживают следующий.

**Перезапуск инстансов.** Редеплой Vercel поднимает новые инстансы сам — отдельный
рестарт не нужен. Публикационный мост на Render кредитов не касается и продолжает
работать; если он перезапускался вручную, перепроверьте публикацию вакансий, но трогать
флаги Render не нужно.

**Если E2E не прошёл** — не оставляйте трафик включённым: верните флаги в `false`
(команда выше с `--value false` + редеплой), откатите деплой Vercel на предыдущий и
разберитесь. Откат кода описан в §3.

### Шаг 7. Наблюдение 24–48 часов

Раз в день выполнять V5 и смотреть на долю `unknown` — рост означает, что доставка часто
заканчивается неоднозначно. `unknown` сохраняет списание: автоматический возврат запрещён,
поскольку документ мог быть доставлен. Рост доли требует расследования транспорта,
а не массового возврата кредитов.

---

## 3. Откат

| Ситуация | Действие | Последствия |
|---|---|---|
| Шаг 2 (деплой с гейтами) не нужен или сломался | Откат деплоя в Vercel на предыдущую версию | Таблицы ещё нет, журнал остаётся источником истины, потери данных нет |
| Таблицу нужно убрать, а новый код **ещё не задеплоен** (шаг 2 не выполнен) | `DROP TABLE public.growth_copilot_credit_ops;` | Журнал остаётся источником истины; состояние операций восстанавливается повторным backfill |
| Таблицу нужно убрать, новый код **УЖЕ задеплоен** (обычный случай: шаг 2 выполнен) | **Сначала** откат деплоя, потом `DROP TABLE` | Новый код без реестра закрывает платные операции; откат на код до PR #70 возвращает прежние гонки |
| Backfill сделал не то | Откат деплоя (если новый код в проде), `DROP TABLE`, затем шаг 4 (backfill) заново | Данные журнала не пострадали |
| E2E обоих транспортов не прошёл | Вернуть `COPILOT_ENABLED=false` и `COPILOT_PDF_ENABLED=false` + редеплой, затем откат деплоя Vercel на предыдущий | Платные операции остаются закрытыми, деньги не теряются; миграции остаются как есть (схема аддитивна и старому коду не мешает) |

**Куда откатывать код.** Откатываться нужно на деплой, который **до PR #70** (то есть на
действующий сейчас прод, где кредиты считаются только по журналу). Код PR #70 работает с
реестром, но без привязки к попытке, — откат на него вернул бы гонку двойного списания.
Откатываться на «промежуточный» релиз нельзя: релиз должен быть один.

**Что происходит с зависшими резервированиями.** Если обработчик упал после списания, но
до расчёта, операция остаётся `reserved`. Новый код это чинит: повторный запрос по той же
вакансии через 15 минут принимает уже оплаченную попытку (слот протух, резерв брошен) и
доводит её до конца **без второго списания**; старый владелец рассчитаться уже не может.
Автоматического возврата кредита при неопределённой доставке нет — это осознанно.

Наблюдать такие операции:

```sql
SELECT user_id, ref, kind, state, attempt, updated_at
FROM public.growth_copilot_credit_ops
WHERE state = 'reserved'
  AND updated_at < now() - interval '15 minutes'
ORDER BY updated_at
LIMIT 50;
```


---

## 4. Риски, которые остаются

1. **R3 — окно backfill.** Описано в §1. Главный риск: двойное списание у одного
   пользователя на одной вакансии. Лечится окном без платных операций.
2. **Недоступность схемы/моста.** Платные операции fail-closed до подтверждения
   реестра; восстановление проверяется раз в 60 секунд. Рестарт обычно не требуется.
3. **Принятие брошенной оплаты.** Актуальный claim может принять `reserved` старше
   15 минут с тем же `attempt`, без списания и без второго `spend`. Старый владелец
   не может рассчитаться после перехвата claim. Однако прежняя отправка могла дойти:
   редкая двойная доставка за один кредит возможна. Это безопаснее двойного списания;
   атомарность внешней доставки и БД этим не обеспечивается. Принятая неоднозначная
   доставка становится `unknown`, кредит не возвращается.
4. **`supabase db push` использовать нельзя** (нет `supabase/config.toml` и истории
   миграций — `db push` попытается применить все файлы P1–P14 и может задеть работающие
   таблицы публикации). Только SQL Editor, Management API или psql — как в
   `docs/RELEASE_RUNBOOK_v7.0.md`, §1.1–1.3.
5. **Проверка релиза начнёт требовать таблицу.** `tools/verify_release_v7.py` уже ждёт
   `growth_copilot_credit_ops` в списке таблиц — до применения миграции он будет давать
   `FAIL`. Это намеренный предохранитель, а не поломка.
6. **Барьер исторических ссылок обязателен.** Если в журнале есть платные операции со
   ссылками `cv`/`pdf` (или любыми другими не-хеш ссылками), выпуск останавливается:
   сопоставление таких операций с вакансиями требует отдельного решения владельца, а
   автоматический перенос создал бы строки-сироты и привёл к повторному списанию.
   Проверка даёт `FAIL` и когда аудит не удалось подтвердить (нет доступа к базе) —
   «не проверил» и «нечего переносить» не одно и то же.
7. **Порядок «деплой раньше миграции».** В окне сначала появляется код, который умеет
   закрывать платные операции на сервере, и только потом — миграции. Обратный порядок
   оставляет окно, в котором старый код продолжает списывать кредиты, а backfill читает
   журнал без их учёта.

---

## 5. Что делать нельзя

- Не применять `supabase db push`.
- Не удалять таблицу, пока новый код в проде (см. §3).
- Не делать repair по журналу после включения платных операций (шаг 6): после этого источником
  истины становится реестр.
- Не пропускать шаг 5 — сверка обязательна.
- Не пропускать шаг 0 — барьер исторических ссылок обязателен **до** P13; не переносить
  не-хеш ссылки в реестр даже при явном разрешении владельца (перенос только через
  согласованное вручную сопоставление).
- Не деплоить релиз с миграциями «одним шагом с миграцией»: сначала флаги `false`, затем
  деплой этого релиза с гейтами, доказательства остановки, и только потом P13/P14.
