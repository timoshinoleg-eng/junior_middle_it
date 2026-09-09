# Production infrastructure — P7

This runbook describes the production topology for `junior_middle_it` after P7.
No credentials belong in the repository.

## Topology

- **Vacancy ingestion/publication:** Vercel Python function `/api/cron`, triggered by GitHub Actions.
- **Public acquisition:** Vercel `/` -> `/api/site`.
- **Durable growth/product state:** Supabase PostgreSQL.
- **Telegram interactive bot:** long-polling runtime (`render_main.py`) only on an always-on host.
- **Vacancy collection dedup/cache:** existing runtime-specific behavior remains unchanged.

Supabase production project:

- project: `junior-middle-it-growth`
- project ref: `uucjptyqfsdzqsftgiro`
- region: Frankfurt / `eu-central-1`

## Current production state — 2026-09-09

- Vercel collector is working: a verified scheduled run fetched 6866 vacancies,
  selected 18 and posted all 18 with `failed=0`.
- Vercel has the Telegram posting credentials required by the collector.
- Supabase schema is applied and healthy, but no durable P7 rows are present yet;
  `GROWTH_DATABASE_URL` still needs to be configured in the Vercel environment.
- Render Free deployment is intentionally fail-closed because the required
  `TELEGRAM_BOT_TOKEN` and `CHANNEL_ID` are not configured there.
- A sleeping/free web service is not a production-safe home for Telegram long
  polling. Use an always-on service for polling, or move the interactive bot to
  a stateless/durable webhook architecture.

## Applied database migrations

The following migrations are already applied to the production Supabase project
and are committed under `supabase/migrations/`:

1. `20260909120448_p7_growth_production_foundation.sql`
2. `20260909121036_p7_product_runtime_tables.sql`

They create durable backend-only state for:

- user settings;
- referrals;
- product/growth events;
- migration metadata;
- public vacancy payloads;
- saved searches.

RLS is enabled and `anon` / `authenticated` receive no table privileges. These
tables are intentionally server-side only; no public Data API policy is required.

## PostgreSQL connection

Use a Supabase PostgreSQL DSN in `GROWTH_DATABASE_URL`.

For a persistent backend on infrastructure without guaranteed IPv6, prefer the
project's Frankfurt Supavisor session-pooler connection. For serverless
functions, use the supported pooler connection recommended by Supabase for that
runtime. Keep SSL enabled and never commit the password or DSN.

The application also accepts `DATABASE_URL` as a compatibility fallback, but
`GROWTH_DATABASE_URL` is the canonical variable for this project.

## Vercel variables

The scheduled ingestion runtime and public P7 landing must use the same durable
vacancy payload store:

```text
TELEGRAM_BOT_TOKEN=...
CHANNEL_ID=...
CRON_SECRET=...
BOT_USERNAME=junior_jobs_channel_bot
GROWTH_DATABASE_URL=postgresql://...
PUBLIC_SITE_URL=https://<production-host>/
```

### Vercel readiness

`/api/health` is deliberately strict:

- `collector_ready=true` means Telegram posting + cron authorization are configured;
- `durable_growth=true` means a PostgreSQL DSN is configured;
- `public_acquisition_ready=true` requires the durable store plus `BOT_USERNAME`;
- HTTP 200 / `ok=true` means the complete P7 serverless contract is configured;
- incomplete P7 configuration returns HTTP 503 without exposing credential values.

The cron endpoint remains usable while durable growth is being wired so existing
vacancy publication is not interrupted. Its response includes
`durable_growth: true|false` for observability.

### Cron authorization

`/api/cron` requires:

```text
Authorization: Bearer <CRON_SECRET>
```

Query-string secrets are not accepted. If `CRON_SECRET`, `TELEGRAM_BOT_TOKEN` or
`CHANNEL_ID` is missing, the endpoint fails before running the expensive source
collection. Internal exceptions are logged/Sentry-captured but the HTTP response
contains only `internal_error`, never a traceback.

## Single scheduler

There must be exactly one production scheduler.

The canonical scheduler is `.github/workflows/vercel-cron.yml`, four times daily:

```text
00:00 UTC
06:00 UTC
12:00 UTC
18:00 UTC
```

It calls `https://junior-middle-it.vercel.app/api/cron` with the GitHub Actions
`CRON_SECRET`. `vercel.json` contains routing only and must not also define a
Vercel cron, otherwise 06:00 UTC can be triggered twice.

`vercel.json` routes:

- `/` -> `/api/site`
- `/robots.txt` -> `/api/robots`

## Render / interactive bot variables

If long polling is retained, the host must be always-on and have:

```text
TELEGRAM_BOT_TOKEN=...
CHANNEL_ID=...
BOT_USERNAME=junior_jobs_channel_bot
ADMIN_USER_ID=...
GROWTH_DATABASE_URL=postgresql://...
REQUIRE_DURABLE_GROWTH=true
```

`render_main.py` fails closed:

- required Telegram env is checked before the HTTP port is bound;
- worker crash/return terminates the process non-zero;
- `/health` returns 200 only while the worker is actually running;
- health output contains presence flags only, not token/channel/DSN values.

Do not enable `REQUIRE_DURABLE_GROWTH=true` until the DSN is verified.

## Legacy SQLite migration

If an always-on interactive host has historical `jobs.db` growth state, migrate
it before enforcing durable-only mode:

```bash
GROWTH_DATABASE_URL='postgresql://...' \
python migrate_growth_to_postgres.py --sqlite jobs.db
```

The migration is transactional and idempotent. Event rows use deterministic
`migration_key` values; a deliberate `--force` retry cannot duplicate migrated
analytics events.

Verification in PostgreSQL should compare source/destination counts for user
settings, referrals and events before removing reliance on legacy growth state.

## P7 production smoke test

1. `/api/health` reports the expected collector/durable/public-acquisition flags.
2. `/` returns the public vacancy landing and `/robots.txt` is present.
3. Category and Junior/Middle filters render only publication-safe jobs.
4. The GitHub Actions cron produces HTTP 200 and reports `durable_growth=true` after DSN activation.
5. Supabase `growth_job_payloads` starts receiving rows after a collection run.
6. `web_home` opens the bot with acquisition attribution.
7. `web_<job_hash>` returns the same vacancy in Telegram.
8. `resume_<job_hash>` enters Resume Match for that vacancy.
9. `/stats_growth` uses first-start/activated mature cohorts.
10. No resume text, credentials or private profile data appears on the public site.

## Rollout order

1. Apply/verify Supabase migrations. **Done.**
2. Keep GitHub Actions as the only cron scheduler. **Done in code.**
3. Configure `GROWTH_DATABASE_URL` in Vercel and verify `/api/health` becomes fully ready.
4. Run one normal scheduled collection and verify `growth_job_payloads` is populated.
5. Choose the interactive runtime: an always-on polling host or durable Vercel webhook.
6. Configure interactive credentials only on that chosen runtime.
7. If legacy growth data exists, migrate it before enforcing durable-only mode.
8. Run the full P7 smoke test.
