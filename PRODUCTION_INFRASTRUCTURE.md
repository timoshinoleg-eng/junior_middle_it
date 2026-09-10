# Production infrastructure — P7

This runbook records the verified production topology for `junior_middle_it`.
No credentials, database passwords, Telegram tokens or private bridge keys belong in the repository.

## Topology

- **Vacancy ingestion/publication:** Vercel Python `/api/cron`, triggered only by GitHub Actions.
- **Public acquisition:** Vercel `/` -> `/api/site`.
- **Durable state:** Supabase PostgreSQL, exposed through narrowly scoped Edge contracts.
- **Vercel -> Supabase writes:** `growth-proxy` job-payload API authenticated with the production Telegram bot identity.
- **Public site reads:** read-only `growth-proxy/public-jobs` projection.
- **Interactive Telegram bot:** long-polling `render_main.py`; requires an always-on host.
- **Render -> Supabase interactive state:** authenticated private `growth-proxy` SQL bridge restricted to `growth_*` relations.

Supabase production project:

- project: `junior-middle-it-growth`
- ref: `uucjptyqfsdzqsftgiro`
- region: Frankfurt / `eu-central-1`

Canonical web production:

- `https://junior-middle-it.vercel.app`

## Verified production state — 2026-09-10

### Vercel

- canonical `/` and `/robots.txt` serve the current application;
- `/api/cron` is Bearer-protected;
- GitHub Actions is the single scheduler at 00/06/12/18 UTC;
- routing uses file-based Python functions with `framework: null`;
- public vacancy landing reads published payloads from Supabase Edge;
- the durable publication protocol v2 has completed a real production cycle with a new vacancy:
  `posted=1`, `failed=0`, and all durable protocol failure counters were zero;
- after that run Supabase contained 20 `published`, 0 `pending`, 0 `sending` job payloads.

### Supabase

Backend-only growth tables cover:

- user settings;
- referrals;
- growth/product events;
- migration metadata;
- vacancy payloads;
- saved searches;
- short-lived interactive runtime state.

RLS is enabled and `anon` / `authenticated` have no DML privileges on backend-only growth tables.

`growth_runtime_state` stores only expiring workflow metadata:

- `setup_step`: 24 hours;
- `resume_session`: 2 hours, **job hash only**;
- `pending_saved_search`: 24 hours, normalized search filters only.

Resume text and PDF/DOCX bytes are never persisted by this runtime-state layer.

### Supabase Edge `growth-proxy`

The production Edge function is protocol v2 and has four separate responsibilities:

1. read-only public `/public-jobs` projection;
2. Telegram-identity-authenticated durable job-payload publication API;
3. protocol-v2 `pending -> sending -> published` state transitions;
4. a private Render growth-state bridge authenticated by a dedicated bridge key.

The private bridge stores only a SHA-256 verifier in source. The raw key exists only in Render environment configuration. Its SQL surface is restricted to `growth_*` relations/local CTEs, one bounded statement per request, with system/Auth/Storage/Vault schemas and privileged SQL rejected.

### Render interactive runtime

Current service:

- service: `junior-middle-it-bot`;
- region: Frankfurt;
- plan: Free;
- branch: `main`;
- start: `python render_main.py`.

Verified startup sequence:

```text
[render_main] durable growth bridge preflight: ok
[render_main] missing required environment: TELEGRAM_BOT_TOKEN
```

Therefore the Render -> Supabase durable bridge is production-proven. `CHANNEL_ID`, `BOT_USERNAME`, durable growth endpoint/key and `REQUIRE_DURABLE_GROWTH=true` are configured. The remaining credential blocker is the interactive runtime's `TELEGRAM_BOT_TOKEN`.

The service is intentionally fail-closed while that token is absent.

**Important:** Render Free sleeps and is not a production-safe long-polling host. Do not treat the interactive bot as live until an always-on polling host is selected or the architecture is intentionally moved to a durable webhook runtime.

## Applied Supabase migrations

Production migration history includes:

1. `20260909120448_p7_growth_production_foundation.sql`
2. `20260909121036_p7_product_runtime_tables.sql`
3. `20260910093335_p7_durable_publication_state.sql`
4. `20260910103116_p7_runtime_state_foundation.sql`
5. `20260910103148_noop_check.sql` — historical no-op marker
6. `20260910103205_p7_runtime_state_cleanup_guard.sql` — historical harmless guard

The two marker migrations are retained in the repository only to keep migration history aligned with production.

## Vercel contract

Production uses the hosted Edge projection rather than exporting a Supabase database password to Vercel.

Operational settings include:

```text
TELEGRAM_BOT_TOKEN=<secret>
CHANNEL_ID=<channel>
CRON_SECRET=<secret>
BOT_USERNAME=junior_jobs_channel_bot
GROWTH_PUBLIC_URL=https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy
```

`PUBLIC_SITE_URL` is optional: the site derives the canonical production URL from Vercel system environment when no explicit override is set.

## Render interactive contract

The persistent polling runtime needs:

```text
TELEGRAM_BOT_TOKEN=<secret>
CHANNEL_ID=@junior_middle_it
BOT_USERNAME=junior_jobs_channel_bot
GROWTH_DATABASE_URL=https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy
GROWTH_HTTP_KEY=<private secret>
REQUIRE_DURABLE_GROWTH=true
ADMIN_USER_ID=<optional admin id>
```

`render_main.py` fails closed:

- private durable growth is preflighted before polling starts;
- missing required Telegram configuration prevents startup;
- worker crash/return terminates the service non-zero;
- `/health` is 200 only while the Telegram worker is actually running;
- health/config output contains presence flags only, never secret values.

## Scheduler

There must be exactly one production vacancy scheduler.

`.github/workflows/vercel-cron.yml` runs at:

```text
00:00 UTC
06:00 UTC
12:00 UTC
18:00 UTC
```

It calls the canonical `/api/cron` with `Authorization: Bearer <CRON_SECRET>`.
`vercel.json` must not define a second cron schedule.

## Production smoke gates

Serverless/public gates:

1. canonical `/` returns the vacancy landing;
2. `/robots.txt` is present;
3. `/api/health` reflects collector/public readiness without exposing secrets;
4. unauthenticated `/api/cron` returns 401;
5. authenticated collector runs return 200;
6. durable protocol ends with no stranded `pending`/`sending` rows;
7. public landing displays only `published` payloads;
8. `web_home`, `web_<hash>` and `resume_<hash>` links are generated correctly.

Interactive gates, to run once an always-on Telegram runtime is enabled:

1. `web_home` records acquisition attribution;
2. `web_<hash>` reconstructs the same durable vacancy;
3. `resume_<hash>` starts Resume Match exactly once;
4. text/PDF/DOCX Resume Match works without persisting resume contents;
5. `/setup` resumes after a worker restart;
6. selected Resume Match vacancy resumes after restart via `job_hash`;
7. pending Saved Search remains available after restart and is consumed after save;
8. `/stats_growth` continues using mature first-start/activation cohorts.

## Remaining production decision

Serverless ingestion, public acquisition and durable storage are operational. The remaining P7 rollout decision is the interactive Telegram runtime:

- keep long polling and move it to an always-on service, **or**
- intentionally redesign it as a durable webhook runtime.

Until that decision is made and the interactive bot token is provisioned on the chosen host, do not claim the Telegram interactive bot is production-live.
