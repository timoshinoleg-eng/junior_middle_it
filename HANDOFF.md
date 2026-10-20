# Handoff — junior_middle_it (v7.0, AI Career Copilot)

**Date:** 2026-10-20
**Repo:** https://github.com/timoshinoleg-eng/junior_middle_it
**Base:** `main` (P7/P8: Supabase Postgres + Edge-мост, webhook-рантайм)

## Status

| Item | State |
|---|---|
| Copilot, этап A (детерминированное ядро) | PR #53, ветка `feat/copilot-core`, CI зелёный |
| Copilot, этап B (PDF-резюме) | PR #54, ветка `feat/copilot-cv`, CI зелёный |
| Copilot, этап C (LLM по кнопке) | PR #55, ветка `feat/copilot-llm`, CI зелёный |
| Copilot, этап D (forward, трекер, радар) | PR #56, ветка `feat/copilot-d`, CI зелёный |
| Copilot, этап E (Mini App) | PR #57, ветка `feat/copilot-webapp`, CI зелёный |
| Copilot, этап F (документация, метрики) | PR #58, ветка `feat/copilot-docs`, CI зелёный |
| Мержи | **делает владелец**, порядок A → B → C → D → E → F |
| Миграции P9–P12 | лежат в `supabase/migrations/`, **не применены** — нужен доступ к Supabase |
| Edge-функция `growth-proxy` | правка валидатора в PR #57, **не задеплоена** |

Стек веток: `feat/copilot-docs` → `feat/copilot-webapp` → `feat/copilot-d` →
`feat/copilot-llm` → `feat/copilot-cv` → `feat/copilot-core` → `main`.
После мержа #53 остальные PR нужно один раз перенацелить на `main`.

## Что появилось в v7.0

1. **Детерминированное ядро Copilot** (`copilot/`): персональный fit-score,
   аудит реального грейда, извлечение HR-контакта, обогащение текста вакансии,
   кредиты и хранилище в Postgres.
2. **PDF-резюме под вакансию** (`fpdf2` + ATS-проверка), кэш `file_id`,
   повторный запрос не списывает кредит.
3. **Опциональный LLM-слой** — только по явной кнопке, с prompt-injection
   guard, бюджетом и гарантированной деградацией к детерминированному отчёту.
4. **Forward-to-Analyze** — пересланная вакансия разбирается тем же ядром,
   дедуп по хэшу текста.
5. **Трекер откликов** — отметка «отклик отправлен», напоминание через 7 дней
   внутри существующей ежедневной доставки, исход «интервью/тишина/отказ».
6. **`/upskill`-радар** — сколько вакансий закрывает стек и чего не хватает.
7. **Telegram Mini App** (`/copilot`) — статика + API с проверкой подписи
   `initData` и rate-limit по пользователю.
8. **Метрики Copilot** в `/stats_growth` (админ).

## Что нужно сделать владельцу (в этом порядке)

1. **Смержить PR** #53 → #54 → #55 → #56 → #57 → #58 (или перенацелить стек
   на `main` и смержить по очереди). CI обязан быть зелёным.
2. **Применить миграции** в Supabase:
   - `20261020120000_p9_copilot_tables.sql`
   - `20261020130000_p10_copilot_ai_reports.sql`
   - `20261020140000_p11_copilot_forward_and_applications.sql`
   - `20261020150000_p12_copilot_rate_limits.sql`
3. **Задеплоить Edge-функцию** `growth-proxy` с правкой валидатора из PR #57
   (иначе upsert-ы `copilot/store.py` отклоняются ключевым прокси).
4. **Задать флаги** в проде (все новые — выключены по умолчанию):
   ```env
   COPILOT_ENABLED=true
   COPILOT_PDF_ENABLED=true
   COPILOT_FORWARD_ENABLED=true
   COPILOT_OUTCOME_ENABLED=true
   COPILOT_UPSKILL_ENABLED=true
   COPILOT_LLM_ENABLED=false        # включать только с ключом провайдера
   COPILOT_LLM_API_KEY=             # только в секретах
   COPILOT_LLM_BASE_URL=https://api.openai.com/v1
   COPILOT_WEBAPP_ENABLED=false     # после регистрации URL у бота
   COPILOT_WEBAPP_URL=https://junior-middle-it.vercel.app/copilot
   ```
5. **Зарегистрировать Mini App** у `junior_jobs_channel_bot` (BotFather →
   Mini App → URL `https://junior-middle-it.vercel.app/copilot`), затем включить
   `COPILOT_WEBAPP_ENABLED`.
6. **Проверить прод** после каждого шага: `/api/health`, `/status`,
   `/stats_growth`, разбор вакансии в личке.

## Статус интерактивного рантайма

| Часть | Где работает | Статус |
|---|---|---|
| Сбор вакансий и публикация | Vercel `/api/cron` + `vercel-cron.yml` | работает |
| Доставка (канал + персональные дайджесты) | Vercel `/api/delivery` + `delivery-cron.yml` | работает |
| Интерактивный бот (webhook) | Vercel `/api/webhook` | работает |
| Интерактивный бот (long polling) | Render | **выключен**: нет `TELEGRAM_BOT_TOKEN`, fail-closed |
| Mini App API | Vercel `/api/copilot` | ждёт применения миграций и регистрации URL |

## Порядок запуска (для новой сессии)

```bash
git clone https://github.com/timoshinoleg-eng/junior_middle_it.git
cd junior_middle_it
git checkout main && git pull
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m compileall -q .
python -m unittest -v test_copilot_runtime test_copilot_webapp_api test_copilot_llm
```

## Известные ограничения и техдолг

1. **Миграции не применены** — до применения P9–P12 код Copilot не должен
   включаться в проде.
2. **LLM без ключа** — этап C написан и покрыт тестами, но без
   `COPILOT_LLM_API_KEY` кнопка AI не показывается.
3. **Mini App без регистрации** — `web_app`-кнопка скрыта, пока не задан
   `COPILOT_WEBAPP_URL` и не включён флаг.
4. **Rate-limit Mini App** — счётчик в Postgres, окно 10 минут; при недоступной
   БД запрос не блокируется (fail-open), кредиты и подпись всё равно защищают.
5. **Edge-мост как бутылочное горлышко** — `growth-proxy` и
   `interactive-growth-proxy` валидируют SQL; новые relations должны
   начинаться с `growth_`.
6. **Bейдж аудита грейда в постах канала не выводится** — решение владельца:
   репутационный риск, бейдж остаётся только внутри бота.
7. **Техдолг (вне плана)**: модули `localized_product_runtime_v2..v8` не
   мигрированы; source health частично in-memory.

## Do not

- не применять миграции в проде до мержа PR #53–#58;
- не включать `COPILOT_LLM_ENABLED` без ключа и без суточного бюджета;
- не публиковать в канал из чего-либо, кроме Vercel production;
- не хранить резюме: текст и байты остаются в памяти запроса;
- не добавлять новый планировщик — для follow-up используется `delivery-cron.yml`;
- не расширять приватный SQL-контракт Edge-моста без теста на allowlist.
