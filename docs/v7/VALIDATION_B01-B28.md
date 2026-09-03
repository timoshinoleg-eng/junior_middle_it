# V7 — Delta-верификация бэклога B01–B28

Дата: 2026-09-03. Бейзлайн: `main` @ `f27c617` (pushed_at 2026-08-15T21:56:53Z,
GitHub API — репозиторий не двигался; `git log f27c617..HEAD` пуст).

Бейзлайн тестов (Фаза 0): **68 тестов, 0 ошибок, 5 skipped** (live-тесты за
`LIVE_SOURCE_TESTS=1`), `python -m unittest discover` → OK.
Окружение сандбокса: Python 3.11.2 (репозиторий таргетит 3.12; в коде
3.12-специфики не обнаружены — зафиксировано, на бейзлайн не влияет).

Все ссылки `[F файл:строка]` даны на состояние `f27c617`.

| ID | Статус | Доказательство |
|----|--------|----------------|
| B01 | CONFIRMED | `vercel.json:3-8` — cron `0 6 * * *`; `.github/workflows/vercel-cron.yml:5-10` — 4 cron-расписания, включая `0 6 * * *`; `render.yaml` `CHECK_INTERVAL=1800` + `render_main.py` цикл через `channel_bot.main()` (`channel_bot.py:4168`). В 06:00 UTC — двойной триггер. |
| B02 | CONFIRMED | `api/cron.py:30-35` — при пустом `CRON_SECRET` аутентификации нет (fail-open); секрет принимается в query: `f"secret={secret}" not in query`. |
| B03 | CONFIRMED | `channel_bot.py:3150-3153` — `check_admin`: `if not Config.ADMIN_USER_ID: return True` (fail-open). |
| B04 | CONFIRMED | `api/cron.py:47-56` — `self._send_json(500, {"ok": False, "error": exc[:2000]})` — traceback наружу. |
| B05 | CONFIRMED | `channel_bot.py:185` — дефолт `0`; `channel_bot.py:1576-1583` — `Config.EMERGENCY_MAX_POSTS_PER_CYCLE or None` → 0 = безлимит; `render.yaml` — `EMERGENCY_MAX_POSTS_PER_CYCLE=0`. |
| B06 | CONFIRMED | `channel_bot.py:2803-2807` — без доступного Telethon-парсера `get_recent_channel_job_hashes()` возвращает пустое множество → серверлесс-дедуп мёртв. |
| B07 | CONFIRMED | Деплой-конфиги: `vercel.json` (cron → `/api/cron` → публикация) и `render.yaml`/`railway.json` (`python channel_bot.py` / `render_main.py` — собственный цикл публикации `channel_bot.py:4168-4323`). Общей транзакционной дедупликации нет. |
| B08 | CONFIRMED | `api/cron.py:38-42` — `collect_and_post_once(use_sqlite=False)`; `channel_bot.py:2969` — `db = init_database() if use_sqlite else None`; `channel_bot.py:2884-2886` — `save_job_payload` только при наличии `db` → под Vercel-постами payload не сохраняется, save/expand в боте отвечают «Вакансия устарела в кэше». |
| B09 | CONFIRMED | `render.yaml` — `plan: free`, без `disk:` → SQLite-файл эфемерен; `render_main.py` док-строка: HTTP-сервер живёт отдельно, сообщения бота не будят сервис. |
| B10 | CONFIRMED | `channel_bot.py:2893-2904` — `any_ok`: успех хотя бы в один канал считается успехом; `register_posted_job` вызывается по `any_ok` (`channel_bot.py:4292`), упавшие каналы не повторяются. |
| B11 | CONFIRMED | `channel_bot.py:2815` — `get_entity(Config.CHANNEL_ID)`: история читается только из главного канала; маршруты `CHANNEL_ROUTES` не учитываются. |
| B12 | CONFIRMED | `channel_bot.py:2871-2874` — `except RetryAfter: await asyncio.sleep(...); return await _send_job_to_chat(...)` — рекурсия без лимита (аналогично `3954-3956`). |
| B13 | CONFIRMED | `migrate_db.py:166-171` — `SELECT hash, title, description, ... FROM posted_jobs`, но в схеме `posted_jobs` (`channel_bot.py:566`, `migrate_db.py:37-48`) колонки `description` нет; ветка `--backfill` (`migrate_db.py:305-306`) не вызывает `sys.exit` → exit 0 при ошибке. |
| B14 | CONFIRMED | `channel_bot.py:3148` — `self.setup_steps: Dict[int, str] = {}` — память процесса. |
| B15 | CONFIRMED | grep по `channel_bot.py`: нет ни `add_error_handler`, ни `set_my_commands` (регистрация хендлеров `channel_bot.py:4063-4093`). |
| B16 | CONFIRMED | `message_formatter.py:283-286` — кнопка `📤 Поделиться` со `switch_inline_query`; `InlineQueryHandler` в `channel_bot.py` не зарегистрирован. |
| B17 | CONFIRMED | `channel_bot.py:3839-3864` — `expand:` вызывает `query.edit_message_text` в исходном сообщении (в канале). |
| B18 | CONFIRMED | `channel_bot.py:3182` — premium обещает «Senior-вакансии в match»; при этом `channel_bot.py:1637-1645` — senior+ исключаются из публикации, `hide_senior` по умолчанию 1 (`channel_bot.py:594`). |
| B19 | CONFIRMED | `channel_bot.py:3725-3743` — `/favorites` без ссылок/удаления/пагинации; `channel_bot.py:3341-3346` — `/digest now` не отличает «пусто» от ошибки отправки (`send_personal_digest` возвращает `int`); `channel_bot.py:3607-3610` — `int(re.sub(r'[^\d]','', 'abc') or '0')` → молча 0. |
| B20 | CONFIRMED | `render_main.py:74-102` — `/health` всегда `send_response(200)` независимо от `ok`; `render_main.py:109-114` — `bot_running=True` сразу после импорта, до старта polling; `api/health.py:7-14` — безусловно 200. |
| B21 | CONFIRMED | `channel_bot.py:1134-1157` — `requests.head(url, allow_redirects=True)` и GET-фолбэк без allowlist доменов и без проверки resolved-IP (private/loopback) до и после редиректа. |
| B22 | CONFIRMED | `channel_bot.py:1595-1632` — `safe_fetch_with_retry` при любом исходе возвращает `[]`, вызывающий код не отличает ошибку от пустого источника; `job_sources_extra.py:585` — `SOURCE_HEALTH = SourceHealthRegistry()` в памяти процесса, сбрасывается на cold start. |
| B23 | CONFIRMED | `extract_posted_date` (`channel_bot.py:1698`) используется только для отображения (`1820-1821`); фильтра по свежести публикации нет; `DEDUP_RETENTION_DAYS=28` (`channel_bot.py:227`) управляет только окном дедупликации. |
| B24 | CONFIRMED | `channel_bot.py:2473-2570` — последовательные запросы по 22+10+13 доскам (Greenhouse/Lever/Ashby) внутри каждого fetcher-а; `collect_and_post_once` обходит источники последовательно (`2996-3012`); `SOURCE_BUDGET_SECONDS=480` в `api/cron.py:41` против ~60 с лимита Vercel Hobby. |
| B25 | CONFIRMED | `requirements.txt` — `requests==2.31.0`, `python-dotenv==1.0.0`; `pyproject.toml` — нет `rapidfuzz`, `sentry-sdk`, `python-telegram-bot[job-queue]` (только базовый `python-telegram-bot==20.6`) — файлы расходятся. |
| B26 | CONFIRMED | `LICENSE` (34 строки) — русский changelog, не лицензионный текст; GitHub API: `license.spdx_id = "NOASSERTION"`; `HANDOFF.md:74` — «PTB LGPLv3», фактически python-telegram-bot 20.6 распространяется под GPL-3.0. |
| B27 | CONFIRMED | `sentry_setup.py` — `environment = "production" if os.getenv("VERCEL") else "railway"` (Render/Railway получают неверный ярлык); `channel_bot.py:27`, `job_sources_extra.py:15` — `xml.etree.ElementTree` для внешнего RSS без `defusedxml`. |
| B28 | CONFIRMED | `.github/workflows/` содержит только `vercel-cron.yml` (триггер публикации); PR-гейтов нет. |

Итог: 28/28 подтверждено, 0 CLOSED, 0 изменившихся.
