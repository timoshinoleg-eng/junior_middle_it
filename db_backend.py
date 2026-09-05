"""Storage backends: SQLite (default) and PostgreSQL (when DATABASE_URL is set).

Why this exists
---------------
On Render's free plan the filesystem is ephemeral: every restart wipes the
SQLite file, so the published-jobs ledger, the delivery ledger and the wizard
state disappear with it. That is the root cause of the duplicate-post incident.
A free external database (Neon's free tier) fixes it without a paid plan.

Design rule: **port the existing schema 1:1, do not redesign it.**
``docs/v7/pg_schema.sql`` proposes a nicer model (job_id instead of job_hash,
JSONB payloads, ...), but adopting it means rewriting all ~65 queries at once.
Porting tables and columns as they are keeps every query and every existing
test working, so the switch is an environment variable instead of a rewrite.
``pg_schema.sql`` stays the aspirational target, not the migration path.

Behaviour without ``DATABASE_URL`` is byte-for-byte the old SQLite behaviour:
the backend is selected only when the variable is present.
"""
import hashlib
import logging
import os
import re
import sqlite3
import threading
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

SQLITE = "sqlite"
POSTGRES = "postgres"

TEST_DATABASE_URL = "TEST_DATABASE_URL"

# Schemas already created during this process (test isolation, see
# PostgresBackend.new_test_schema).
_TEST_SCHEMAS: Set[str] = set()
_TEST_SCHEMAS_LOCK = threading.Lock()


def translate_placeholders(sql: str) -> str:
    """Rewrite SQLite ``?`` placeholders to psycopg ``%s``, ignoring string literals.

    A naive ``sql.replace('?', '%s')`` would corrupt any literal question mark
    inside a quoted string, so quotes are tracked. Doubled ``''`` (an escaped
    quote inside a literal) is handled explicitly.
    """
    out: List[str] = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_string and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_string = not in_string
            out.append(ch)
        elif ch == "?" and not in_string:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# Tables written with SQLite's ``INSERT OR IGNORE`` / ``INSERT OR REPLACE``.
# The conflict target is the unique constraint that makes the upsert a no-op
# (IGNORE) or an overwrite (REPLACE) on PostgreSQL. Tables not listed here are
# left untouched (and would be a configuration error worth catching in CI).
_UPSERT_CONFLICT: dict = {
    "deliveries": ("job_hash", "target_channel"),
    "user_favorites": ("user_id", "job_hash"),
    "posted_jobs": ("hash",),
    "job_payloads": ("hash",),
    "meta": ("key",),
    "bot_state": ("key",),
}


def translate_upsert(sql: str) -> str:
    """Rewrite SQLite upserts to PostgreSQL ``ON CONFLICT`` clauses.

    Applied only on the Postgres path (``SQLiteBackend.prepare`` is a no-op,
    so SQLite keeps its native ``INSERT OR IGNORE/REPLACE``). Returns the
    input unchanged for any statement that is not one of those two shapes.

    The conflict clause is appended at the end of the statement (the only
    valid position in PostgreSQL); ``OR IGNORE``/``OR REPLACE`` is stripped
    and replaced by ``ON CONFLICT (cols) DO NOTHING`` / ``DO UPDATE SET ...``.
    """
    # INSERT OR IGNORE INTO t (...) VALUES (...) -> ON CONFLICT (cols) DO NOTHING
    m = re.match(r"^\s*INSERT OR IGNORE INTO (\w+)\b", sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        if table not in _UPSERT_CONFLICT:
            return sql
        conflict = ", ".join(_UPSERT_CONFLICT[table])
        out = re.sub(r"INSERT OR IGNORE ", "INSERT ", sql, count=1, flags=re.IGNORECASE)
        return out.rstrip(";") + f" ON CONFLICT ({conflict}) DO NOTHING"
    # INSERT OR REPLACE INTO t (a, b) VALUES (...) ->
    #   ON CONFLICT (conflict) DO UPDATE SET <non-conflict cols> = EXCLUDED.<...>
    m = re.match(r"^\s*INSERT OR REPLACE INTO (\w+)\s*\(([^)]+)\)", sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        if table not in _UPSERT_CONFLICT:
            return sql
        cols = [c.strip() for c in m.group(2).split(",")]
        conflict = list(_UPSERT_CONFLICT[table])
        set_cols = [c for c in cols if c not in conflict]
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in set_cols)
        out = re.sub(r"INSERT OR REPLACE ", "INSERT ", sql, count=1, flags=re.IGNORECASE)
        return (
            out.rstrip(";")
            + f" ON CONFLICT ({', '.join(conflict)}) DO UPDATE SET {set_clause}"  # nosec B608  (cols from hardcoded _UPSERT_CONFLICT map)
        )
    return sql


class Backend:
    """Dialect-specific bits. Subclasses supply DDL and a handful of expressions."""

    name = SQLITE
    placeholder = "?"
    operational_errors: Tuple[type, ...] = (sqlite3.OperationalError,)

    def connect(self, dsn: str):
        raise NotImplementedError

    def prepare(self, sql: str) -> str:
        return sql

    def ts_ago(self, minutes: int) -> str:
        """SQL expression for 'now minus N minutes' (used by the delivery claim)."""
        minutes = max(0, int(minutes))
        return f"datetime('now', '-{minutes} minutes')"

    def current_timestamp(self) -> str:
        return "CURRENT_TIMESTAMP"

    def columns(self, cursor, table: str) -> Set[str]:
        raise NotImplementedError

    def tables(self, cursor) -> Set[str]:
        raise NotImplementedError

    def ddl(self) -> List[str]:
        raise NotImplementedError

    def new_test_schema(self, conn, key: str) -> None:
        """No-op on SQLite: every test already gets its own temp file."""


class SQLiteBackend(Backend):
    name = SQLITE
    placeholder = "?"
    # OperationalError covers lock contention and connection-level failures;
    # other DatabaseError subclasses (e.g. malformed SQL) must not be retried
    # as if the database disappeared.
    operational_errors = (sqlite3.OperationalError,)

    def connect(self, dsn: str):
        conn = sqlite3.connect(dsn, check_same_thread=False, timeout=30)
        # Reduce transient `database is locked` failures when two publisher
        # threads/processes touch the same legacy SQLite file. WAL setup is
        # deliberately deferred until the serialized schema-init phase.
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def columns(self, cursor, table: str) -> Set[str]:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def tables(self, cursor) -> Set[str]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cursor.fetchall()}

    def ddl(self) -> List[str]:
        return list(_SQLITE_DDL)


class PostgresBackend(Backend):
    name = POSTGRES
    placeholder = "%s"
    operational_errors: Tuple[type, ...] = ()

    def __init__(self, dsn: str):
        self.dsn = dsn
        try:
            import psycopg2  # noqa: F401  (imported lazily, dependency is optional)
            import psycopg2.extensions  # noqa: F401
        except ImportError as exc:  # pragma: no cover - configuration error
            raise RuntimeError(
                "DATABASE_URL is set but psycopg2 is not installed; "
                "add psycopg2-binary to requirements.txt"
            ) from exc
        import psycopg2

        self.operational_errors = (
            psycopg2.OperationalError,
            psycopg2.InterfaceError,
        )

    def connect(self, dsn: str):
        import psycopg2

        url = dsn or self.dsn
        # Neon (and most hosted Postgres) require TLS; a URL without sslmode
        # would connect in the clear or be refused.
        if "sslmode=" not in url:
            url = url + ("&" if "?" in url else "?") + "sslmode=require"
        conn = psycopg2.connect(url, connect_timeout=15)
        # The app commits explicitly; keep psycopg2's default transactional mode.
        return conn

    def prepare(self, sql: str) -> str:
        # placeholders first (turns `?` into `%s`), then SQLite upserts into
        # PostgreSQL `ON CONFLICT` clauses.
        return translate_upsert(translate_placeholders(sql))

    def ts_ago(self, minutes: int) -> str:
        minutes = max(0, int(minutes))
        return f"(now() - interval '{minutes} minutes')"

    def columns(self, cursor, table: str) -> Set[str]:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}

    def tables(self, cursor) -> Set[str]:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
        return {row[0] for row in cursor.fetchall()}

    def ddl(self) -> List[str]:
        return list(_POSTGRES_DDL)

    def new_test_schema(self, conn, key: str) -> None:
        """Isolate a test database in its own schema.

        Tests ask for ``DatabaseConnection(<temp file path>)``. Under
        ``TEST_DATABASE_URL`` the same path maps to the same schema, so two
        connections opened by one test (the concurrency test) still share
        state, while separate tests never see each other's rows. The schema is
        dropped once per process, on first use.
        """
        # Non-security use: derive an isolated Postgres schema name from the
        # test's db_path. No secret/integrity dependence on the hash.
        schema = "t_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]  # nosec B324
        with _TEST_SCHEMAS_LOCK:
            fresh = schema not in _TEST_SCHEMAS
            _TEST_SCHEMAS.add(schema)
        cur = conn.cursor()
        if fresh:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        # schema name is derived from an md5 hex digest - no injection surface.
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")  # nosec B608
        cur.execute(f"SET search_path TO {schema}")  # nosec B608
        conn.commit()
        cur.close()


# --------------------------------------------------------------------------
# Schema, ported 1:1 from the historical SQLite layout.
#
# Notes on the PostgreSQL port:
#   * boolean-ish flags stay SMALLINT, because the code passes 0/1 ints and
#     PostgreSQL would reject an int literal for a BOOLEAN column;
#   * INTEGER PRIMARY KEY AUTOINCREMENT becomes BIGSERIAL;
#   * TIMESTAMP is valid in both dialects, so it is kept as-is.
# --------------------------------------------------------------------------

_SQLITE_DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS posted_jobs (
        hash TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        level TEXT,
        url TEXT,
        source TEXT,
        category TEXT DEFAULT 'other',
        posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_hash TEXT NOT NULL,
        saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, job_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        enabled_categories TEXT DEFAULT 'development,qa,devops,data,marketing,sales,pm,design,other',
        hide_senior BOOLEAN DEFAULT 1,
        min_salary_filter INTEGER DEFAULT 0,
        skills TEXT DEFAULT '',
        digest_enabled INTEGER DEFAULT 0,
        onboarding_done INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_content_hashes (
        hash TEXT PRIMARY KEY,
        source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id INTEGER,
        name TEXT NOT NULL,
        props TEXT DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS referrals (
        user_id INTEGER PRIMARY KEY,
        referrer_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_payloads (
        hash TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deliveries (
        job_hash TEXT NOT NULL,
        target_channel TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_hash, target_channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        fetched INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        elapsed_ms INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_category ON posted_jobs(category)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON posted_jobs(posted_at)",
    "CREATE INDEX IF NOT EXISTS idx_favorites_user ON user_favorites(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tg_hashes_created ON telegram_content_hashes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_name_ts ON events(name, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_payloads_created ON job_payloads(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status)",
    "CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs(source, started_at)",
]

_POSTGRES_DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS posted_jobs (
        hash TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        level TEXT,
        url TEXT,
        source TEXT,
        category TEXT DEFAULT 'other',
        posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_favorites (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        job_hash TEXT NOT NULL,
        saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, job_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id BIGINT PRIMARY KEY,
        enabled_categories TEXT DEFAULT 'development,qa,devops,data,marketing,sales,pm,design,other',
        hide_senior SMALLINT DEFAULT 1,
        min_salary_filter INTEGER DEFAULT 0,
        skills TEXT DEFAULT '',
        digest_enabled SMALLINT DEFAULT 0,
        onboarding_done SMALLINT DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_content_hashes (
        hash TEXT PRIMARY KEY,
        source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        user_id BIGINT,
        name TEXT NOT NULL,
        props TEXT DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS referrals (
        user_id BIGINT PRIMARY KEY,
        referrer_id BIGINT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_payloads (
        hash TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deliveries (
        job_hash TEXT NOT NULL,
        target_channel TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_hash, target_channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_runs (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        fetched INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        elapsed_ms INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_category ON posted_jobs(category)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON posted_jobs(posted_at)",
    "CREATE INDEX IF NOT EXISTS idx_favorites_user ON user_favorites(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tg_hashes_created ON telegram_content_hashes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_name_ts ON events(name, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_payloads_created ON job_payloads(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status)",
    "CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs(source, started_at)",
    # v7 ledger queries are always (job_hash, target_channel) lookups.
    "CREATE INDEX IF NOT EXISTS idx_deliveries_job ON deliveries(job_hash, target_channel)",
]


def database_url() -> Optional[str]:
    """Effective DSN: test override first, then the production variable."""
    return (
        os.getenv(TEST_DATABASE_URL)
        or os.getenv("DATABASE_URL")
        or None
    )


def get_backend(url: Optional[str] = None) -> Backend:
    """Pick a backend. No URL (the default) means SQLite, i.e. today's behaviour."""
    url = url if url is not None else database_url()
    if not url:
        return SQLiteBackend()
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresBackend(url)
    raise RuntimeError(
        f"Unsupported DATABASE_URL scheme: {url.split('://', 1)[0]!r} "
        "(expected postgresql://…)"
    )


def is_postgres(backend: Optional[Backend]) -> bool:
    return bool(backend) and backend.name == POSTGRES
