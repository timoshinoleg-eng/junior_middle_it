# Production infrastructure — P7

This runbook describes the production topology for `junior_middle_it` after P7.
No credentials belong in the repository.

## Topology

- **Telegram interactive bot:** persistent Python process (`render_main.py`).
- **Public acquisition + scheduled ingestion:** Vercel Python functions.
- **Durable growth/product state:** Supabase PostgreSQL.
- **Vacancy collection dedup/cache:** existing runtime-specific behavior remains unchanged.

Supabase production project:

- project: `junior-middle-it-growth`
- project ref: `uucjptyqfsdzqsftgiro`
- region: Frankfurt / `eu-central-1`

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
project's Frankfurt Supavisor **session pooler** connection. For serverless
functions, use the supported pooler connection recommended by Supabase for that
runtime. Keep SSL enabled and never commit the password or DSN.

The application also accepts `DATABASE_URL` as a compatibility fallback, but
`GROWTH_DATABASE_URL` is the canonical variable for this project.

## Render / interactive bot variables

Required production variables:

```text
TELEGRAM_BOT_TOKEN=...
CHANNEL_ID=...
BOT_USERNAME=junior_jobs_channel_bot
ADMIN_USER_ID=...
GROWTH_DATABASE_URL=postgresql://...
REQUIRE_DURABLE_GROWTH=true
```

`render.yaml` declares the sensitive values with `sync: false`; set them in the
hosting environment. Enable `REQUIRE_DURABLE_GROWTH=true` only after the DSN has
been verified, because this intentionally makes startup fail closed if durable
storage cannot be reached.

Health verification after deploy:

- `/health` returns `ok: true`;
- `config.GROWTH_DATABASE` is `true`;
- `config.REQUIRE_DURABLE_GROWTH` is `true`;
- `config.PUBLIC_ACQUISITION` is `true`;
- no PostgreSQL connection error is present.

## Vercel variables

The scheduled ingestion runtime and the public P7 landing must use the same
durable vacancy payload store:

```text
GROWTH_DATABASE_URL=postgresql://...
BOT_USERNAME=junior_jobs_channel_bot
CHANNEL_ID=...
PUBLIC_SITE_URL=https://<production-host>/
```

Existing Telegram/cron variables remain required for `/api/cron`.

`vercel.json` routes:

- `/` -> `/api/site`
- `/robots.txt` -> `/api/robots`
- daily cron -> `/api/cron`

## Legacy SQLite migration

If the live interactive host has historical `jobs.db` growth state, migrate it
before enforcing durable-only mode:

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

1. Open `/` and confirm a 200 HTML page.
2. Open `/robots.txt` and confirm `/` is allowed while `/api/` is disallowed.
3. Confirm category and Junior/Middle filters render only publication-safe jobs.
4. Open the landing's main Telegram CTA (`web_home`) and confirm the bot starts.
5. Open an individual `web_<job_hash>` CTA and confirm the same vacancy is shown in Telegram.
6. Open `resume_<job_hash>` and confirm Resume Match immediately requests resume text.
7. Check `/stats_growth`: web starts appear in acquisition attribution and cohort metrics remain bounded by the first-start cohort.
8. Confirm no resume text or user profile data is rendered by the public site.

## Rollout order

1. Apply/verify Supabase migrations.
2. Configure `GROWTH_DATABASE_URL` on the persistent bot host and Vercel.
3. Run the legacy SQLite growth migration if historical state exists.
4. Deploy the bot and verify PostgreSQL-backed health/runtime.
5. Set `REQUIRE_DURABLE_GROWTH=true` on the persistent bot host.
6. Deploy/verify Vercel public landing and cron.
7. Run the P7 smoke test and inspect `/stats_growth` after real attributed starts.
