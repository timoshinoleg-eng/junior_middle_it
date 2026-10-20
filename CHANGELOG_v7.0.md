# CHANGELOG v7.0 — AI Career Copilot

Дата: 2026-10-20. Ветки: `feat/copilot-core` → `feat/copilot-cv` →
`feat/copilot-llm` → `feat/copilot-d` → `feat/copilot-webapp` →
`feat/copilot-docs`.

v7.0 не меняет редакционный пайплайн и не меняет издателя в канал. Это
надстройка поверх действующей архитектуры: Postgres в Supabase, Edge-мост
`growth-proxy`, webhook-рантайм бота, единственный издатель из Vercel
production.

## Этап A — детерминированное ядро Copilot (PR #53)

- Пакет `copilot/`: `fit.py` (персональный fit-score), `grade_audit.py`
  (аудит реального грейда), `hr_link.py` (контакт HR и deep-link),
  `enrich.py` (догрузка текста вакансии по внешней ссылке), `store.py`
  (хранилище), `credits.py` (кредиты).
- Команда `/copilot`, deep-link `?start=copilot_<hash>`, кнопка
  «✨ Разбор вакансии» в постах канала.
- Кнопки «Написать HR» и «Сохранить такой поиск».
- Миграция `20261020120000_p9_copilot_tables.sql`: семь таблиц
  `growth_copilot_*` с RLS и `REVOKE` для `anon`/`authenticated`.
- Кредиты: суточный бесплатный лимит, реферальные начисления, глобальный
  суточный бюджет, атомарное списание.

## Этап B — PDF-резюме под вакансию (PR #54)

- `copilot/pdf.py`: генерация одностраничного CV на `fpdf2`, ATS-проверка
  (`pypdf`), извлечение контактов.
- Шрифт `copilot/fonts/DejaVuSans.ttf` с лицензией DejaVu.
- Кэш `file_id` в `growth_copilot_documents`: повторный запрос отдаёт файл
  без новой сборки и без списания кредита.
- Новый флаг `COPILOT_PDF_ENABLED`.

## Этап C — опциональный LLM-слой (PR #55)

- `copilot/security.py`: вырезание инструкций из недоверенного текста (EN/RU),
  редакция контактов, обёртка `<untrusted_job_post>`, проверка «выдуманных»
  навыков.
- `copilot/llm.py`: OpenAI-совместимый клиент на стандартной библиотеке,
  строгий JSON-ответ, ограниченные секции, деградация к детерминированному
  отчёту при любой ошибке.
- Кнопка «🤖 Разобрать глубже (AI)» — только по явному действию; по умолчанию
  текст резюме в модель не уходит, контакты вакансии редактируются.
- Миграция `20261020130000_p10_copilot_ai_reports.sql` (кэш ответов).
- Флаги: `COPILOT_LLM_ENABLED`, `COPILOT_LLM_API_KEY`, `COPILOT_LLM_BASE_URL`,
  `COPILOT_LLM_MODEL`, `COPILOT_LLM_TIMEOUT`, `COPILOT_LLM_MAX_TOKENS`,
  `COPILOT_LLM_CACHE_HOURS`, `COPILOT_LLM_SEND_RESUME`,
  `COPILOT_LLM_DAILY_BUDGET`.

## Этап D — forward, трекер откликов, upskill-радар (PR #56)

- `copilot/forward.py`: перехват пересланной вакансии; команды и документы не
  перехватываются, активный шаг `/setup` забирает ввод себе, текст похожий на
  резюме не анализируется; дедуп по `job_hash` от нормализованного текста.
- `copilot/outcome.py`: состояния отклика и напоминание.
- `copilot/upskill.py`: радар по базе вакансий (покрытие стеком, дефицит
  навыков, медиана зарплаты).
- Трекер откликов: «✉️ Отклик отправлен» → `growth_copilot_applications` →
  напоминание через 7 дней **внутри существующей ежедневной доставки**
  (`delivery-cron.yml`), ответы «интервью / тишина / отказ».
- `/upskill` и campaign payload `?start=upskill_<code>` для кнопки «Поделиться».
- Миграция `20261020140000_p11_copilot_forward_and_applications.sql`.

## Этап E — Telegram Mini App (PR #57)

- `miniapp/` — статическая страница с вкладками Fit / Резюме / Поиск; маршрут
  `/copilot` задан rewrite-ом в `vercel.json`.
- `copilot/webapp_auth.py`: проверка HMAC-подписи `initData`, окно свежести
  24 часа, подписанный пользователь.
- `api/copilot.py`: `POST /api/copilot` (fit, сохранённые поиски) и
  `POST /api/copilot/pdf` (CV под вакансию, кэш без списания).
- Rate-limit по `user_id` (окно 10 минут), миграция
  `20261020150000_p12_copilot_rate_limits.sql`.
- Кнопка «🖥 Открыть Mini App» — при `COPILOT_WEBAPP_ENABLED` и заданном
  `COPILOT_WEBAPP_URL`.
- Попутно: `growth-proxy` теперь игнорирует `SET` в
  `ON CONFLICT ... DO UPDATE SET`, как интерактивный прокси.

## Этап F — документация и метрики (PR #58)

- `README.md` переписан под фактическую архитектуру (Vercel + GitHub Actions,
  Supabase + Edge-мост, webhook-рантайм, Postgres вместо SQLite).
- `HANDOFF.md` — новая версия: статус PR, порядок мержа, чек-лист владельца.
- `CHANGELOG_v7.0.md`, `docs/COPIOT.md`, `SECURITY.md`.
- `GROWTH_PLAN.md`: отмечены выполненные пункты P0, добавлен раздел про
  метрики Copilot; non-goal «no LLM dependency» сохранён — LLM только за флагом.
- Метрики Copilot в `/stats_growth` (админ): разборы, сохранённые поиски,
  выдачи CV, переходы к HR, доля откатов LLM на детерминированный отчёт.

## Общее

- Новые зависимости: `trafilatura>=1.12,<3` (этап A), `fpdf2>=2.7,<3` (этап B).
  LLM-SDK не добавлялся — используется стандартная библиотека.
- Новые тесты: `test_copilot_fit`, `test_copilot_grade_audit`,
  `test_copilot_hr_link`, `test_copilot_enrich`, `test_copilot_store`,
  `test_copilot_credits`, `test_copilot_runtime`, `test_copilot_pdf`,
  `test_copilot_security`, `test_copilot_llm`, `test_copilot_forward`,
  `test_copilot_outcome`, `test_copilot_upskill`, `test_copilot_webapp_auth`,
  `test_copilot_webapp_api` — все офлайн, зарегистрированы в allowlist CI.
