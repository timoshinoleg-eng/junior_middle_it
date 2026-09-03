-- v7 Stage 1 (prep): target PostgreSQL schema for the unified state.
-- Status: PROPOSAL for owner review (D-05 in DECISION_LOG.md). Not wired yet.
-- Migration path: single Alembic chain replacing the three competing paths
-- (_initialize_database / run_hash_migration / migrate_db.py).

CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    TEXT NOT NULL UNIQUE,          -- generate_job_hash()
    title           TEXT NOT NULL,
    company         TEXT NOT NULL DEFAULT '',
    level           TEXT,                          -- Junior | Middle
    category        TEXT NOT NULL DEFAULT 'other',
    url             TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    salary          TEXT NOT NULL DEFAULT '',
    payload         JSONB NOT NULL,                -- full job JSON (B08: saved BEFORE posting)
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_category_level ON jobs (category, level);

CREATE TABLE IF NOT EXISTS job_sources (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'api'        -- api | rss | telegram | ats
);

CREATE TABLE IF NOT EXISTS deliveries (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    target_channel  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | sent | failed
    attempts        INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    sent_at         TIMESTAMPTZ,
    UNIQUE (job_id, target_channel)                 -- B07/B10: per-target transactional dedup
);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries (status) WHERE status <> 'sent';

-- Publisher loop (Stage 1): SELECT ... FROM deliveries WHERE status='pending'
-- FOR UPDATE SKIP LOCKED LIMIT n  → concurrent workers never double-post.
-- Collection cycle guard: SELECT pg_advisory_lock(hashtext('collect_cycle')).

CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    referrer_id BIGINT REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id             BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    enabled_categories  TEXT NOT NULL DEFAULT 'development,qa,devops,data,marketing,sales,pm,design,other',
    hide_senior         BOOLEAN NOT NULL DEFAULT TRUE,
    min_salary_filter   INT NOT NULL DEFAULT 0,
    skills              TEXT NOT NULL DEFAULT '',
    digest_enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    onboarding_done     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS favorites (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    job_id      BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'saved',     -- saved | applied | interview | offer | rejected
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS wizard_states (           -- B14: persistent /setup FSM
    user_id     BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    step        TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_runs (             -- B22: persistent source health
    id          BIGSERIAL PRIMARY KEY,
    source_id   INT NOT NULL REFERENCES job_sources(id),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    fetched     INT NOT NULL DEFAULT 0,
    error       TEXT,
    elapsed_ms  INT
);
CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     BIGINT,
    name        TEXT NOT NULL,
    props       JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS outbox (                  -- reliable message dispatch
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,                       -- dm | channel_post
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ,
    attempts    INT NOT NULL DEFAULT 0
);
