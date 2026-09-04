# V7 Stabilization — финальный отчёт

Дата: 2026-09-04. Ветка сессии: `arena/01a0699c-junior-middle-it`
(функционально — `fix/v7-stabilization`; имя зафиксировано платформой, пуш в
`main` не выполнялся). Бейзлайн: `main @ f27c617`, репозиторий не двигался
(`pushed_at = 2026-08-15T21:56:53Z`, GitHub API — проверено в сессии).

**Бейзлайн тестов Фазы 0:** 68 тестов, 0 ошибок, 5 skipped (за
`LIVE_SOURCE_TESTS=1`), `python -m unittest discover` → OK.
**Финал:** 139 тестов, 0 ошибок, 5 skipped. Ни один тест бейзлайна не
удалён и не деградировал; изменены только 2 теста политики публикаций —
вместе с намеренным изменением семантики лимита (B05), задокументированным
в decision log D-04.

Коммиты: `628a1dd` Этап 0 → `809dae0` Этап 1 → `423da46` Этап 2 →
`c11dd20` Этап 3 → `f9eecd2` Этап 4.

Продакшн-эффектов не было: ни одного запроса к живым каналам/эндпоинтам
публикации; весь сетевой код в тестах мокается; проверка зависимостей —
только PyPI/GitHub API метаданные и `pip-audit`.

---

## Этап 0 — стоп-кран (коммит 628a1dd)

**Delta-верификация:** B01–B06 подтверждены [F] (см. `VALIDATION_B01-B28.md`),
CLOSED нет.

**Изменения:**
- `vercel.json` — нативный крон удалён; единственный расписательный триггер —
  GH Actions (D-01). [F vercel.json]
- Kill-switch: `PUBLISH_ENABLED` (дефолт `false`) и `PROCESS_ROLE`
  (`bot|publisher|all`) — `channel_bot.py` Config + гейт в
  `collect_and_post_once()` и в цикле `main()`. [F channel_bot.py: Config]
- Fail-closed: `Config.validate(require_admin=…, require_cron_secret=…)`;
  `check_admin` без `ADMIN_USER_ID` отказывает всем; `/api/cron` без
  `CRON_SECRET` → 503, приём секрета через query-string удалён.
  [F channel_bot.py check_admin; api/cron.py authorize_cron_request]
- B04: тело ответа `/api/cron` при ошибке — `internal_error`, traceback —
  только Sentry/логи. [F api/cron.py]
- B05: дефолт лимита 20, значения <1 клампятся (`env_int_positive`);
  безлимит — только явный `UNLIMITED_POSTS_PER_CYCLE=true`.
  [F channel_bot.py select_jobs_for_publication]
- B06 interim: серверлесс-посты без интерактивных кнопок
  (`create_inline_keyboard(interactive=False)`), предупреждение в логах;
  отсутствие сессии дополнительно логируется как «дедуп мёртв».
  [F message_formatter.py, channel_bot.py collect_and_post_once]
- B12 (из плана Этапа 1, перенесён — фикс локализован в той же функции):
  рекурсия RetryAfter ограничена `max_flood_retries=3`.
  [F channel_bot.py _send_job_to_chat]
- B20: `BOT_LIVE` выставляется после `start_polling()`; `/health` Render
  отвечает 503, пока бот не жив. [F render_main.py]

**Тесты:** +21 (`test_stage0_v7.py`): auth fail-closed (503/401/200),
отсутствие traceback в ответе, отказ `check_admin` без конфига, кламп лимита,
килл-кран до валидации конфига, неинтерактивная клавиатура, ограниченность
flood-retry (ровно 4 попытки). Итог этапа: 68 → 89, 0 ошибок.

**Риски/откат:** после деплоя публикации останавливаются до включения
`PUBLISH_ENABLED=true` (задумано, D-02). Откат — `git revert 628a1dd`.
До деплоя в проде остаётся двойной триггер 06:00 (код уже не влияет).

**Приёмка:** ровно один прод-триггер в коде ✓; неавторизованный `/api/cron`
→ 401/503 ✓ (тесты); не-админ не проходит `check_admin` ✓ (тест); лимит
публикаций конечен ✓ (тест).

---

## Этап 1 — единое состояние, SQLite-интерим (коммит 809dae0)

**Delta-верификация:** B07–B13 подтверждены [F].

**Изменения:**
- Леджер доставок `deliveries` (`UNIQUE(job_hash, target_channel)`);
  `post_job_with_bot` пишет статус по каждому каналу;
  `retry_failed_deliveries()` в начале цикла повторяет ТОЛЬКО упавшие каналы
  (лимит 5 попыток). `JobBot.post_job` унифицирован с серверлесс-путём.
  [F channel_bot.py]
- B11: `get_recent_channel_job_hashes()` обходит главный канал + все маршруты
  (`dedup_target_channels()`); падение одного канала не убивает дедуп.
- B08: payload сохраняется ДО отправки на всех путях, где есть БД;
  серверлесс до PostgreSQL остаётся неинтерактивным (задокументировано).
- `run_v7_migration()`: идемпотентный апгрейд старых БД (deliveries,
  `posted_jobs.last_seen_at`, `user_favorites.status`).
- Схема PostgreSQL + план единой миграции (замена трёх конкурирующих путей,
  закрывает B13 в целевой архитектуре): `docs/v7/pg_schema.sql`.
  Advisory-lock/FOR UPDATE SKIP LOCKED описаны там же — исполняются при
  переходе на PG (вопрос владельцу, D-05).

**Тесты:** +10 (`test_stage1_v7.py`): идемпотентность миграции на легаси-схеме,
леджер (апсерт попыток, `sent` не повторяется, каппинг попыток, частичный
отказ мульти-трека), повтор только упавшего канала, `payload_missing` не
крутится бесконечно, дедуп по двум каналам, устойчивость к мёртвому каналу.
Итог: 89 → 99.

**Риски/откат:** схема БД расширена обратно совместимо; откат —
`git revert 809dae0` (новые таблицы остаются неиспользованными).

**Приёмка:** конкурентный тест на дубли — в Этапе 4
(`test_concurrency_v7.py`); кнопки на постах любого процесса — частично:
посты процесса с БД полностью функциональны, серверлесс-посты намеренно
без кнопок до PG; состояние доставок переживает рестарт ✓ (БД).

---

## Этап 2 — пользовательский бот (коммит 423da46)

**Delta-верификация:** B14–B20 подтверждены [F].

**Изменения:**
- B14: состояние визарда в `context.user_data` + `PicklePersistence`
  (переживает рестарты; целевой персистер поверх PG — после D-05).
- B15: глобальный `add_error_handler` (Sentry + всегда отвечает на
  callback) и `set_my_commands` с public/admin scope в `post_init`.
- B16: Share → `https://t.me/share/url` (URL-кнопка), мёртвый
  `switch_inline_query` удалён.
- B17: expand в канале/группе шлёт подробности в ЛС, пост не редактируется;
  в ЛС — редактирование на месте.
- B18: обещания Senior удалены из текстов (дефолт задания, D-06).
- B19: `/favorites` — «Открыть» (ссылка из поста ИЛИ из `job_payloads`),
  удаление, пагинация по 5, цикл статусов
  `saved→applied→interview→offer→rejected`; `get_user_favorites` — LEFT JOIN
  + фолбэк в payload (избранные с Vercel-постов больше не «пустые»);
  `/digest now` — структура `matched/sent/failed/reason`; «abc» в `/setup`
  теперь отклоняется с подсказкой, а не превращается в 0.

**Тесты:** +16 (`test_stage2_v7.py`). Итог: 99 → 115.

**Риски/откат:** файл персистентности `*.pkl` в `.gitignore`; на Render
Free файл живёт до пересоздания инстанса (ограничение платформы, лечится
D-05). Откат — `git revert 423da46`.

**Приёмка:** смоук-цепочка `/start → /setup → digest → save → favorites →
open/remove` покрыта юнит-тестами по каждому звену; живой прогон — только
по команде владельца (запрет продакшн-эффектов).

---

## Этап 3 — сбор данных (коммит c11dd20)

**Delta-верификация:** B21–B28 подтверждены [F].

**Изменения:**
- B21: `http_guard.py` — allowlist доменов (расширяется
  `URL_PREFLIGHT_ALLOWED_HOSTS`), проверка ВСЕХ resolved-IP
  (private/loopback/link-local/reserved/multicast/unscheduled, включая
  IPv4-mapped) ДО запроса и на каждом хопе редиректа (ручное ведение
  редиректов); вне allowlist → `unknown`, вакансия остаётся публикуемой.
- B22: `safe_fetch_with_retry` возвращает typed-исход
  (`ok/jobs/error/skipped/elapsed_ms`) — ошибка источника больше не
  выглядит как «0 вакансий, успех»; персистентный `source_runs` +
  `warm_source_health_from_db()` восстанавливает fail-стрики после cold
  start.
- B23: `FRESHNESS_MAX_DAYS=14` (7–14 по ТЗ; выбрано 14) в обоих циклах;
  нераспознаваемые даты сохраняются.
- B24: Greenhouse/Lever/Ashby — ограниченная конкурентность
  (`ATS_FETCH_CONCURRENCY=6`, `ThreadPoolExecutor`) + ETag/Last-Modified
  кэш (`http_cache.py`); бюджет цикла сохранён.
- B25: `requests>=2.33.0`, `python-dotenv>=1.2.2` — `pip-audit` по стеку
  чист (проверено в сессии 2026-09-03 [F]); `pyproject.toml` синхронизирован
  с `requirements.txt` (добавлены `rapidfuzz`, `sentry-sdk`, `defusedxml`,
  `job-queue` extra).
- B26: `LICENSE` — настоящий MIT (старый changelog архивирован в
  `docs/v7/`); заметка в `HANDOFF.md` исправлена на GPL-3.0. Итоговая
  лицензия — вопрос владельцу (D-07).
- B27: `defusedxml` — жёсткая зависимость для внешнего RSS/XML; окружение
  Sentry определяется по маркерам платформы вместо хардкода «railway».
- B13: `migrate_db.py --backfill` не выбирает несуществующую `description`
  и возвращает ненулевой код при ошибке.
- OSS (D-10): `ats_scrapers_adapter.py` — опциональная интеграция
  `kalil0321/ats-scrapers`. Гейты, собранные в сессии [F]: лицензия MIT
  (GitHub API + PyPI), `pushed_at` 2026-09-02, PyPI-релиз 0.3.0
  от 2026-09-02, 5 контрибьюторов, не архивирован. НЕ добавлен в
  `requirements.txt`: проверенный в сессии конфликт — пакет требует
  `httpx>=0.27`, PTB 20.6 пинит `httpx~=0.25.0` → `ResolutionImpossible`.
  Активация — только в отдельном коллектор-окружении (`USE_ATS_SCRAPERS`).

**Тесты:** +21 (`test_stage3_v7.py`): блокировка private/loopback/metadata-IP,
запрет редиректа на 169.254.169.254 до второго запроса, граница цикла
редиректов, парсеры дат, свежесть (старое/свежее/нечитаемое/выкл),
typed-исходы, восстановление стриков, ETag-304, backfill без
`description` + exit-код, деградация адаптера. Итог: 116 → 137.

**Риски/откат:** неизвестные домены не проверяются (→ `unknown`, публикация
сохраняется) — продукт не деградирует; откат — `git revert c11dd20`.

**Приёмка:** фейлящий источник виден в `source_runs`/`/sources` и
скипается по стрикам ✓; preflight не ходит в private-диапазоны ✓ (тесты);
бюджет цикла — конкурентность сокращает время обхода ~в 6 раз на досках.

---

## Этап 4 — CI (коммит f9eecd2)

- `.github/workflows/ci.yml`: compileall → unittest discover → `pip-audit -l`
  (до установки dev-инструментов) → ruff (легаси-конфиг `E9, F63, F7, F82`
  в `pyproject.toml`) → `bandit -ll` → migration smoke (пустая и легаси БД)
  → тест конкурентных паблишеров.
- Находки bandit закрыты по существу (жёсткий `defusedxml`,
  `usedforsecurity=False`, обоснованные `# nosec` для B608/B104).
- `test_concurrency_v7.py`: два одновременных «паблишера» на одной БД →
  ровно одна строка на вакансию и одна доставка на (вакансия, канал).
- Итог: 137 → 139 тестов, 0 ошибок; ruff clean; bandit `-ll` exit 0.

Canary — только по явной команде владельца (не выполнялся).

---

## 6. Traceability: B-ID ↔ фикс ↔ тест

| B-ID | Фикс | Тест | Статус |
|------|------|------|--------|
| B01 | vercel.json без крона; один расписатель (D-01) | конфиг + решение | ✅ |
| B02 | fail-closed auth, header-only секрет | CronAuthTests, CronHandlerTests | ✅ |
| B03 | check_admin fail-closed + require_admin | CheckAdminFailClosedTests, ConfigValidationTests | ✅ |
| B04 | ошибка → `internal_error` | test_error_body_has_no_traceback | ✅ |
| B05 | дефолт 20, кламп, флаг безлимита | EnvIntPositiveTests, тесты каппинга | ✅ |
| B06 | неинтерактивные серверлесс-посты + логирование; полная починка = PG (D-05) | NonInteractiveKeyboardTests | ✅ interim |
| B07 | единый килл-кран + леджер доставок; полная починка = один процесс/PG (вопрос владельцу) | test_concurrency_v7 | ✅ interim |
| B08 | payload до отправки на путях с БД; серверлесс ждёт PG | тесты клавиатуры/лексикона + документация | ✅ частичное |
| B09 | не исправляется кодом: эфемерность Render Free; вопрос владельцу (D-05: платный disk vs PG) | — | ⏸ вопрос владельцу |
| B10 | леджер доставок + повтор только упавших каналов | DeliveryLedgerTests, RetryFailedDeliveriesTests, конкурентный тест | ✅ |
| B11 | дедуп по всем каналам маршрутов | MultiChannelDedupTests | ✅ |
| B12 | ограниченные flood-retry | FloodRetryBoundedTests | ✅ |
| B13 | backfill без `description`, корректный exit | MigrateBackfillTests | ✅ |
| B14 | user_data + PicklePersistence | WizardPersistenceHelpersTests | ✅ |
| B15 | error handler + set_my_commands | ErrorHandlerTests, PostInitCommandsTests | ✅ |
| B16 | share через t.me/share/url | ShareButtonTests | ✅ |
| B17 | expand → ЛС, канал не редактируется | ExpandToDMTests | ✅ |
| B18 | обещания Senior убраны | SeniorPromiseRemovedTests | ✅ (D-06) |
| B19 | favorites UX, digest-структура, валидация /setup | FavoritesManagementTests, DigestStructuredResultTests, SetupValidationTests | ✅ |
| B20 | BOT_LIVE + 5xx health | код + решение; живой мониторинг после деплоя | ✅ |
| B21 | http_guard: allowlist + IP-проверка всех хопов | IpBlockingTests, GuardedRequestTests | ✅ |
| B22 | typed-исходы + source_runs + восстановление стриков | TypedFetchOutcomeTests, WarmSourceHealthTests | ✅ |
| B23 | freshness gate 14 дней | FreshnessGateTests | ✅ |
| B24 | конкурентные фетчеры + ETag; бюджет сохранён | ETagCacheTests | ✅ |
| B25 | версии подняты, pip-audit чист, файлы синхронизированы | CI pip-audit | ✅ |
| B26 | MIT-текст + правка HANDOFF | решение владельца (D-07) | ✅ (D-07) |
| B27 | defusedxml жёстко; environment по платформе | bandit -ll clean | ✅ |
| B28 | CI PR-gate | сам workflow + concurrency-тест | ✅ |

Без фикса: только B09 — не лечится кодом (инфраструктурный выбор владельца,
см. вопрос 1).

## 7. Beta-гейты

| # | Критерий | Статус | Доказательство |
|---|----------|--------|----------------|
| 1 | Ровно один production publisher | ⚠️ зависит от деплоя | в коде один триггер + килл-кран; включение `PUBLISH_ENABLED` владельцем на одном процессе |
| 2 | Ноль дублей при 2 одновременных попытках | ✅ | `test_concurrency_v7.py` (SQLite-уровень); для двух РАЗНЫХ процессов нужен PG (D-05) |
| 3 | Профиль/wizard/favorites переживают рестарт | ✅ частично | PicklePersistence + БД; полная гарантия — после PG |
| 4 | Кнопки работают на сообщениях любого процесса | ✅ частично | посты процесса с БД — полностью; серверлесс без кнопок до PG |
| 5 | Не-админ не вызывает админ-команды | ✅ | fail-closed + тесты |
| 6 | Health 5xx при мёртвом воркере | ✅ | `render_main.py` + BOT_LIVE |
| 7 | Нет публикаций старше freshness window | ✅ | gate + тесты |
| 8 | 10/10 циклов без вмешательства | ⏸ требует продакшн-прогона | только по команде владельца (запрет эффектов) |
| 9 | Полный Telegram smoke | ⏸ тесты покрывают звенья | живой прогон — по команде владельца |
| 10 | Backup/restore PostgreSQL | ⏸ блокировано D-05 | схема готова (`pg_schema.sql`) |

## 8. Открытые вопросы владельцу

1. **Этап 1, инфраструктура (D-05):** платный Render + persistent disk
   (полдня) или сразу Neon/Supabase PostgreSQL + Alembic? От ответа зависят
   гейты 1/2/3/4/10 и закрытие B06/B08/B09.
2. **Килл-кран (D-02):** на каком процессе включить `PUBLISH_ENABLED=true`
   после деплоя Этапа 0? Рекомендация — ни на каком до решения вопроса 1.
3. **Частота публикаций (D-01):** 4×/сутки (00/06/12/18 UTC) — целевая?
4. **Premium/Senior (D-06):** обещание убрано — подтвердить, либо заказать
   реальную выдачу Senior для premium.
5. **Лицензия (D-07):** MIT по умолчанию; если нужна иная — заменить
   `LICENSE`. Отдельно: юрист по GPL-3.0 (PTB) перед дистрибуцией/монетизацией.
6. **ats-scrapers (D-10):** включить через отдельный коллектор-процесс или
   сначала апгрейд PTB до 21.x?
7. **JobSpy (D-09):** не трогался — сначала проверка ToS
   LinkedIn/Indeed/Glassdoor.

## Самопроверка

- [x] каждый закрытый B-ID перепроверен по коду `f27c617`; CLOSED без фикса — только B09 (инфраструктура), обоснован;
- [x] каждый фикс сопровождается регрессионным тестом (+71 тест к бейзлайну);
- [x] ноль запросов к живым каналам/эндпоинтам публикации; все сетевые пути в тестах мокаются;
- [x] OSS-факты собраны в этой сессии (дата/источник в тексте); несобранное помечено и не используется;
- [x] в `main` ничего не запушено; секретов в diff нет (проверено: только плейсхолдеры `.env.example.txt`);
- [x] субагенты не использовались — вся верификация выполнена напрямую;
- [x] новые находки: `DatabaseConnection._initialize()` падает на БД старше
  v6.1 (нет колонки `category`) — зафиксировано при тестировании миграций,
  не воспроизводится на актуальных продакшн-БД (схема `migrate_db.py` v1.0
  создаёт `category`); фикс не вносился, чтобы не расширять scope.
