# v6.1 — Growth P0 (quality + acquisition hooks)

## Code
- `growth_utils.py` — fuzzy fingerprint, salary→USD min, referral deep-link helpers
- `channel_bot.py` — events/referrals tables, fuzzy+exact dedup, source diversify, `/start` CTA + `/ref`, daily digest JobQueue, salary gate
- `telegram_job_parser.py` — FloodWait retry, jitter between channels
- `requirements.txt` — `rapidfuzz`, `python-telegram-bot[job-queue]`
- `test_growth_utils.py` — unit tests (no bot token)

## Env (new)
`BOT_USERNAME`, `FUZZY_DEDUP_*`, `ENABLE_SOURCE_DIVERSIFY`, `GLOBAL_MIN_SALARY_USD`, `ENABLE_DAILY_DIGEST`, `DIGEST_*`, `TELEGRAM_CHANNEL_DELAY`

## Deploy notes
1. `pip install -r requirements.txt`
2. Set `BOT_USERNAME` to bot username without `@`
3. Restart long-running process (digests need JobQueue; serverless cron path still uses `collect_and_post_once` with diversify+fuzzy)
4. SQLite auto-migrates `fingerprint`, `events`, `referrals` on start

## Not in this release (P1+)
Mini App, resume matching, model2vec, HH client swap — next milestones.
