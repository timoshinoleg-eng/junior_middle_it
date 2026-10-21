# P13 — применение миграции реестра операций кредитов (junior_middle_it)

Документ для **вашей** подготовки и применения в проде. Ничего из этого я не применял сам:
ни миграции, ни деплоя, ни merge. Все команды написаны так, чтобы вы могли выполнить их
одну за другой и проверить результат на каждом шаге.

---

## 0. Зачем это нужно (коротко)

PR #70 (коммит `98313ae`) закрывает гонку, из-за которой **два параллельных запроса по одной
вакансии могли списать у пользователя два кредита**, а чужой расчёт мог **вернуть кредит при
неоднозначной доставке** (нарушение fail-closed).

Защита работает **только если в проде есть таблица** `public.growth_copilot_credit_ops`.
Без неё код молча работает по старой схеме (по журналу `growth_copilot_credit_events`), и гонка
остаётся — в логах при этом будет только предупреждение, а не ошибка.

Поэтому порядок строго такой: **сначала миграция (аддитивная, безопасная), потом деплой кода**.
Откат деплоя при этом возможен и ничего не теряет — таблица лишняя для старого кода.

---

## 1. Окно работ и блокировка платных операций

| | |
|---|---|
| Длительность окна | 10–20 минут |
| Что нужно остановить | Источник платных операций: Telegram-бот (экспорт PDF) и/или webhook-обработчик, пока он работает со старым кодом |
| Как долго | До шага 5 (деплой) — старый код продолжает писать только в журнал, и эти операции не попадут в реестр |
| Что можно делать параллельно | Всё, что не пишет кредиты: публикация вакансий, просмотр вакансий, рефералы |

**Почему окно нужно.** Backfill читает журнал одним снимком (`READ COMMITTED`). Если в этот
момент старый код записывает новую резервацию в журнал, её в реестре не будет — а после
деплоя новый код посчитает, что операция не оплачена, и спишет кредит **второй раз**.

Три способа избежать этого — выберите один:

1. **Остановить платные операции** на время шагов 3–5 (самый надёжный вариант).
2. **Не останавливать, но сделать repair** (шаг 3.2) сразу после деплоя и до первого
   повторного запроса по этим вакансиям — рискованно, окно между деплоем и repair лучше
   сделать нулевым.
3. **Окно на запись одной операции**: если поток платных операций мал, дождаться, пока все
   текущие операции завершатся (PDF генерируется за секунды), и выполнить backfill сразу
   после этого.

---

## 2. Порядок (обязательный)

### Шаг 1. Снимок состояния «до»

```sql
-- 1.1 Есть ли уже таблица (если NULL — миграция ещё не применялась)
SELECT to_regclass('public.growth_copilot_credit_ops') AS table_exists;

-- 1.2 Сколько событий в журнале (это и есть «истина» по кредитам)
SELECT count(*) AS journal_events FROM public.growth_copilot_credit_events;

-- 1.3 Распределение причин (для понимания, что именно переносится в реестр)
SELECT reason, count(*) FROM public.growth_copilot_credit_events GROUP BY 1 ORDER BY 2 DESC;

-- 1.4 Открытые резервации ( spends > releases ) — именно они станут строками реестра
WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
    GROUP BY 1, 2, 3
)
SELECT s.user_id, s.ref, s.kind, s.c AS spends, COALESCE(r.c, 0) AS releases
FROM spends s
LEFT JOIN releases r ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
WHERE s.c > COALESCE(r.c, 0)
ORDER BY s.user_id, s.ref;
```

Сохраните вывод 1.4 — он понадобится на шаге 4 для сверки.

### Шаг 2. DDL (можно применить заранее, в любой момент)

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

### Шаг 3. Backfill (только в окне)

#### 3.1 Основной backfill

```sql
BEGIN;

-- Блокировка на время переноса: две одновременные сессии не будут мешать друг другу.
SELECT pg_advisory_xact_lock(20261021);

WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS spends
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS releases
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
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
ON CONFLICT (user_id, ref, kind) DO NOTHING;

COMMIT;
```

#### 3.2 Repair (если backfill уже выполнялся, а в журнал дописались новые операции)

**Только до деплоя нового кода.** После деплоя источником истины становится реестр, и repair
по журналу может испортить его.

```sql
BEGIN;
SELECT pg_advisory_xact_lock(20261021);

WITH spends AS (
    SELECT user_id, ref, reason AS kind, COUNT(*) AS spends
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
    GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS releases
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
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
ON CONFLICT (user_id, ref, kind) DO UPDATE
    SET state = EXCLUDED.state,
        attempt = EXCLUDED.attempt,
        updated_at = NOW()
WHERE public.growth_copilot_credit_ops.state <> EXCLUDED.state;

COMMIT;
```

### Шаг 4. Сверка (обязательно — не пропускайте)

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
    WHERE reason IN ('spend', 'llm_call') GROUP BY 1, 2, 3
), releases AS (
    SELECT user_id, ref,
           CASE reason WHEN 'spend_release' THEN 'spend' ELSE 'llm_call' END AS kind,
           COUNT(*) AS c
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release') GROUP BY 1, 2, 3
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
```

Если V2, V3 или V5 вернули строки — **не продолжайте**. Сначала разберитесь (чаще всего это
значит, что в окно шла платная операция и нужен repair из шага 3.2).

### Шаг 5. Деплой кода

Порядок: сначала R2-фикс и независимое ревью → merge PR в `main` → Vercel задеплоит сам.
На момент деплоя миграция уже применена, поэтому новая защита включится сразу.

### Шаг 6. Проверка после деплоя

```sql
-- Реестр наполняется (ожидается > 0, если идёт трафик)
SELECT count(*) FROM public.growth_copilot_credit_ops WHERE updated_at > now() - interval '1 hour';

-- Состояния после деплоя
SELECT state, count(*) FROM public.growth_copilot_credit_ops GROUP BY 1 ORDER BY 2 DESC;

-- Нет расхождений баланса
SELECT u.user_id, u.balance, COALESCE(e.delta_sum, 0) AS journal_sum
FROM public.growth_copilot_credits u
LEFT JOIN (SELECT user_id, SUM(delta) AS delta_sum
           FROM public.growth_copilot_credit_events GROUP BY 1) e USING (user_id)
WHERE u.balance <> COALESCE(e.delta_sum, 0);
```

**И в логах прод-инстансов не должно быть** строки
`The credit operation registry is missing` (`copilot/store.py:1300`).
Если она есть — процессы работают на небезопасном fallback, и нужен рестарт инстансов.

### Шаг 7. Наблюдение 24–48 часов

Раз в день выполнять V5 и смотреть на долю `unknown` — рост означает, что доставка часто
заканчивается неоднозначно и кредиты честно «зависают» (это правильное поведение fail-closed,
но вернуть их должен бот, а не человек).

---

## 3. Откат

| Ситуация | Действие | Последствия |
|---|---|---|
| Деплой не нужен или сломался | Откат деплоя в Vercel на предыдущую версию | Таблица остаётся, старый код её не читает. Потеря данных нет |
| Таблицу нужно убрать, новый код НЕ задеплоен | `DROP TABLE public.growth_copilot_credit_ops;` | Журнал остаётся источником истины; состояние операций придётся восстановить повторным backfill |
| Таблицу нужно убрать, новый код УЖЕ задеплоен | **Сначала** откат деплоя, потом `DROP TABLE` | Пока код новый, а таблицы нет — код уйдёт в небезопасный fallback, и гонка вернётся |
| Backfill сделал не то | `DROP TABLE` + шаги 2 и 3 заново (только если новый код не задеплоен) | Данные журнала не пострадали |

---

## 4. Риски, которые остаются

1. **R3 — окно backfill.** Описано в §1. Главный риск: двойное списание у одного
   пользователя на одной вакансии. Лечится окном без платных операций.
2. **R4 — липкий fallback.** Если процесс работал до применения миграции, он мог
   запомнить «таблицы нет» и не подхватить реестр без рестарта. Деплой это лечит (новые
   инстансы), но любой долгоживущий воркер надо перезапустить. Проверка — шаг 6 (логи).
3. **Fail-open до миграции.** Пока таблицы нет, защита от двойного списания не работает.
   Значит: **миграцию применять до того, как в прод попадёт новый код.**
4. **`supabase db push` использовать нельзя** (нет `supabase/config.toml` и истории
   миграций — `db push` попытается применить все файлы P1–P13 и может задеть работающие
   таблицы публикации). Только SQL Editor, Management API или psql — как в
   `docs/RELEASE_RUNBOOK_v7.0.md`, §1.1–1.3.
5. **Проверка релиза начнёт требовать таблицу.** `tools/verify_release_v7.py` уже ждёт
   `growth_copilot_credit_ops` в списке таблиц — до применения миграции он будет давать
   `FAIL`. Это намеренный предохранитель, а не поломка.

---

## 5. Что делать нельзя

- Не применять `supabase db push`.
- Не удалять таблицу, пока новый код в проде (см. §3).
- Не делать repair по журналу после деплоя нового кода.
- Не пропускать шаг 4 — сверка обязательна.
- Не применять миграцию и не деплоить в один шаг: сначала миграция, потом код.
