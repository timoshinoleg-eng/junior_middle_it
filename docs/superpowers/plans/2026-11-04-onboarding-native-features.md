# Onboarding + нативные фичи Telegram: финальная рабочая версия

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в бота недостающие нативные сценарии из обсуждения доработок — избранное по deep link из канала, лид-магнит за подписку на канал, оплату AI-кредитов через Telegram Stars — встроив их в существующий PTB-рантайм без нарушения его privacy-инварианта.

**Architecture:** Новый модуль `onboarding_flow.py` с изолированными handler-функциями (чистые, тестируемые без Postgres). Интеграция — по устоявшемуся паттерну `copilot_webhook_runtime.py`: monkey-patch цепочки `durable.JobBot.cmd_start` / `handle_callback` / `base.build_runtime`. Все новые callback_data используют префикс `obn:`, чтобы не пересекаться с существующими (`copilot_`, `growth_`, `resume_`, `share_`).

**Tech Stack:** Python 3.12, python-telegram-bot 20.6, CopilotStore/CreditManager (psycopg/Supabase), unittest.

## Global Constraints

- Репозиторий: `timoshinoleg-eng/junior_middle_it`, ветка `main`, деплой — Vercel (auto-deploy на push), CI — `.github/workflows/ci.yml` (`python -m unittest` по явному списку модулей).
- **Privacy-инвариант продукта: резюме пользователя никогда не сохраняется в БД** (`copilot_webhook_runtime._unmatched_resume_text`, `copilot/store.py: save_evaluation`). Поэтому сценарий «загрузил резюме → бот запомнил его» из обсуждения **не реализуем** — вместо него vacancy-first флоу, который уже есть в продукте.
- Существующие deep link payload'ы не ломаем: `ref_` (рефералки), `copilot_<hash>`, `upskill_`, `share_`, `resume_<hash>`, `web_home`.
- `CopilotStore` создаётся только через `CopilotStore.from_database(job_bot.db)` — никогда `CopilotStore()` напрямую.
- `save_profile` имеет keyword-only сигнатуру `(user_id, *, grade, skills, source)`; `get_document/save_document` — хранилище сгенерированных CV и к резюме отношения не имеют. Не используем их для резюме.
- `CreditManager.grant_referral(user_id, amount: Optional[int])` — второй аргумент это количество (int), не строка.
- Каждый новый тест-модуль обязан быть добавлен в список `.github/workflows/ci.yml`.

## Контракты репозитория (проверены чтением кода)

- `job_bot.db.get_job_payload(job_hash) -> Optional[Dict]` — карточка вакансии.
- `job_bot.db.add_favorite(user_id: int, job_hash: str) -> bool` — `durable_product_state.DatabaseConnection` (наследует p7).
- `job_bot.db.get_user_settings(user_id) / save_user_settings(user_id, values)` — JSON-настройки (там храним флаги `magnet_claimed`, `stars_charge_ids`).
- `CopilotStore.from_database(db) -> Optional[CopilotStore]`; `store.ensure_credit_row(user_id)`; `store.grant_credits_with_event(user_id, amount, reason, ref) -> bool`.
- `context.bot.get_chat_member(chat_id, user_id)` — статус подписки; `context.bot.send_invoice(...)` (PTB 20.6, `provider_token=""` для XTR).
- `PreCheckoutQueryHandler`, `filters.SUCCESSFUL_PAYMENT` — из `telegram.ext`.
- `copilot_webhook_runtime._load_job(job_bot, job_hash)`, `_send_analysis(job_bot, update, job_hash)` — переиспользуем.

---

### Task 1: Модуль `onboarding_flow.py`

**Files:**
- Create: `onboarding_flow.py`
- Test: `test_onboarding_flow.py`

**Interfaces:**
- Produces:
  - `DEEPLINK_FAV_PREFIX = "fav_"`, `DEEPLINK_MAGNET = "magnet"`, `DEEPLINK_STARS = "stars"`
  - `CB_CLAIM_MAGNET = "obn:claim_magnet"`, `CB_BUY_CREDITS = "obn:buy_credits"`
  - `async handle_start_extras(self, update, context) -> bool` — True если deep link обработан
  - `async handle_callback_extras(self, update, context) -> bool` — True если callback обработан
  - `async cmd_stars(self, update, context)`
  - `async precheckout_handler(update, context) -> None`
  - `async successful_payment_handler(update, context) -> None`
  - `async def grant_credits_once(store, db, user_id, amount, reason, idem_key) -> bool`

**Шаги:**

- [ ] **Step 1: Написать failing-тесты** (см. Task 2 — тесты пишутся первыми, TDD)

- [ ] **Step 2: Реализовать `onboarding_flow.py`**

Ключевые функции:
- `handle_start_extras`: маршрутизация `fav_<hash>` (add_favorite + подтверждение с кнопкой «✨ Разбор вакансии» deep link `copilot_<hash>`), `magnet` (проверка подписки), `stars` (инвойс). Возвращает False для чужих payload'ей.
- `_check_subscription(bot, user_id)`: `get_chat_member`; любая ошибка API → True (не блокируем).
- `_deliver_magnet(self, update, context)`: если `settings.magnet_claimed` → только гайд; иначе grant 3 кредитов (`reason="magnet_bonus"`, idem-флаг в настройках), гайд + лог события `magnet_claimed`.
- `cmd_stars` / `_send_invoice`: `send_invoice(provider_token="", currency="XTR", prices=[LabeledPrice("Пакет AI-кредитов (10 шт.)", 50)], payload=f"stars_topup_{user_id}")`.
- `precheckout_handler`: `answer_pre_ok=True` только если payload начинается с `stars_topup_`.
- `successful_payment_handler`: грант 10 кредитов, идемпотентность по `telegram_payment_charge_id` (список в настройках пользователя).
- `grant_credits_once(store, db, user_id, amount, reason, idem_key)`: без store → False; `ensure_credit_row` + `grant_credits_with_event`.

- [ ] **Step 3: Прогнать тесты** — `python -m unittest -v test_onboarding_flow` → PASS

- [ ] **Step 4: Commit**

### Task 2: Тесты `test_onboarding_flow.py`

**Files:**
- Create: `test_onboarding_flow.py`

**Interfaces:**
- Consumes: контракты из Task 1; FakeDb/FakeBot по образцу `test_copilot_runtime.FakeDb`.

- [ ] **Step 1: Тест-кейсы**
  - `fav_` с известным hash → `add_favorite` вызван, ответ содержит подтверждение.
  - `fav_` с неизвестным hash → «устарела», избранное не тронуто.
  - чужой payload (`ref_123`) → `handle_start_extras` вернул False.
  - `magnet`, не подписан → сообщение с кнопкой подписки, кредиты не начислены.
  - `magnet`, подписан, первый раз → +3 кредита, `magnet_claimed=True` в настройках.
  - повторный `claim_magnet` → кредиты не начислены второй раз.
  - `cmd_stars` → `send_invoice` с `currency="XTR"`, `provider_token=""`, payload `stars_topup_<uid>`.
  - pre_checkout с валидным payload → `answer(ok=True)`; с чужим → `ok=False`.
  - successful_payment → +10 кредитов; повтор тот же `charge_id` → не начислены.

- [ ] **Step 2: Run** — ожидаем FAIL до реализации, PASS после.

### Task 3: Интеграция в `copilot_webhook_runtime.py`

**Files:**
- Modify: `copilot_webhook_runtime.py` (цепочки патчей `_copilot_cmd_start`, `_copilot_handle_callback`, `_copilot_build_runtime`)

- [ ] **Step 1: Подключить `handle_start_extras` в `_copilot_cmd_start`** до fallback на `_original_cmd_start`.
- [ ] **Step 2: Подключить `handle_callback_extras` в `_copilot_handle_callback`** до проверки `CALLBACK_PREFIX` (чужие callback'и → False → штатное поведение).
- [ ] **Step 3: В `_copilot_build_runtime` добавить** `CommandHandler("stars", ...)`, `PreCheckoutQueryHandler`, `MessageHandler(filters.SUCCESSFUL_PAYMENT, ...)`.
- [ ] **Step 4: Проверить импорт** — `python -c "import copilot_webhook_runtime"` → без ошибок.
- [ ] **Step 5: Прогнать смежные тесты** — `python -m unittest -v test_copilot_runtime test_api_webhook_contract test_callback_runtime_hotfix` → PASS

### Task 4: CI + деплой

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Добавить `test_onboarding_flow` в список unittest-ов ci.yml** (после `test_copilot_runtime`).
- [ ] **Step 2: Локально** — `python -m compileall -q .`
- [ ] **Step 3: Commit + push в `main`** — CI запустится на push; Vercel задеплоит production автоматически (vercel.json + git-интеграция).

## Assumptions (auto mode)

- Деплой осуществляется через git-интеграцию Vercel на push в main; прямого доступа к дашборду Vercel у агента нет — проверка деплоя через CI-статус и доступность webhook-эндпоинта.
- Если `git push` без stored credentials не пройдёт — использовать GitHub MCP (`push_files` / PR) как fallback.
- Сценарий «сохранение резюме в боте» намеренно не реализуется из-за privacy-инварианта продукта; вместо этого используется существующий vacancy-first флоу.
