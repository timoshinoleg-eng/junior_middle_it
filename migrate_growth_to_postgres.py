#!/usr/bin/env python3
"""One-shot migration of durable growth state from jobs.db to PostgreSQL.

Usage:
    GROWTH_DATABASE_URL=postgresql://... python migrate_growth_to_postgres.py

The migration is transactional and records a marker in PostgreSQL. Re-running a
completed migration is a no-op unless --force is passed. Even with --force,
legacy events are idempotent through a deterministic migration key, so retrying
a partially verified rollout cannot duplicate analytics history.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from growth_store import PostgresGrowthStore


DEFAULT_CATEGORIES = [
    "development", "qa", "devops", "data", "marketing", "sales", "pm",
    "design", "support", "security", "other",
]
MIGRATION_MARKER = "sqlite_growth_v1"


def sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row)


def migrate(sqlite_path: str, dsn: str, force: bool = False) -> dict:
    path = Path(sqlite_path)
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    if not dsn:
        raise RuntimeError("GROWTH_DATABASE_URL/DATABASE_URL is required")

    # Ensure the baseline destination schema exists before the migration
    # transaction. Production Supabase migrations add the same compatibility
    # columns/indexes explicitly; these ALTERs keep standalone PostgreSQL safe.
    bootstrap = PostgresGrowthStore(dsn, DEFAULT_CATEGORIES)
    bootstrap.close()

    source = sqlite3.connect(str(path))
    source.row_factory = sqlite3.Row
    counts = {"users": 0, "referrals": 0, "events": 0}

    try:
        with psycopg.connect(dsn, autocommit=False) as target:
            with target.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS growth_migration_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE growth_events ADD COLUMN IF NOT EXISTS migration_key TEXT"
                )
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_events_migration_key "
                    "ON growth_events(migration_key)"
                )
                cur.execute(
                    "SELECT value FROM growth_migration_meta WHERE key = %s",
                    (MIGRATION_MARKER,),
                )
                marker = cur.fetchone()
                if marker and not force:
                    return {**counts, "status": "already_migrated", "marker": marker[0]}

                if table_exists(source, "user_settings"):
                    cols = sqlite_columns(source, "user_settings")
                    rows = source.execute("SELECT * FROM user_settings").fetchall()
                    for row in rows:
                        def value(name, default=None):
                            return row[name] if name in cols else default

                        cats = value("enabled_categories", "") or ",".join(DEFAULT_CATEGORIES)
                        cur.execute(
                            """
                            INSERT INTO growth_user_settings (
                                user_id, enabled_categories, hide_senior, min_salary_filter,
                                skills, digest_enabled, onboarding_done, alerts_enabled,
                                premium_unlocked, updated_at
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, NOW()))
                            ON CONFLICT (user_id) DO UPDATE SET
                                enabled_categories=EXCLUDED.enabled_categories,
                                hide_senior=EXCLUDED.hide_senior,
                                min_salary_filter=EXCLUDED.min_salary_filter,
                                skills=EXCLUDED.skills,
                                digest_enabled=EXCLUDED.digest_enabled,
                                onboarding_done=EXCLUDED.onboarding_done,
                                alerts_enabled=EXCLUDED.alerts_enabled,
                                premium_unlocked=EXCLUDED.premium_unlocked,
                                updated_at=EXCLUDED.updated_at
                            """,
                            (
                                int(row["user_id"]), str(cats), bool(value("hide_senior", 1)),
                                int(value("min_salary_filter", 0) or 0), str(value("skills", "") or ""),
                                bool(value("digest_enabled", 0)), bool(value("onboarding_done", 0)),
                                bool(value("alerts_enabled", 0)), bool(value("premium_unlocked", 0)),
                                value("updated_at"),
                            ),
                        )
                        counts["users"] += 1

                if table_exists(source, "referrals"):
                    rows = source.execute(
                        "SELECT user_id, referrer_id, created_at FROM referrals"
                    ).fetchall()
                    for row in rows:
                        if not row["user_id"] or not row["referrer_id"] or row["user_id"] == row["referrer_id"]:
                            continue
                        cur.execute(
                            """
                            INSERT INTO growth_referrals (user_id, referrer_id, created_at)
                            VALUES (%s, %s, COALESCE(%s, NOW()))
                            ON CONFLICT (user_id) DO NOTHING
                            RETURNING user_id
                            """,
                            (int(row["user_id"]), int(row["referrer_id"]), row["created_at"]),
                        )
                        if cur.fetchone():
                            counts["referrals"] += 1

                if table_exists(source, "events"):
                    rows = source.execute(
                        "SELECT id, ts, user_id, name, props FROM events ORDER BY id"
                    ).fetchall()
                    for row in rows:
                        try:
                            props = json.loads(row["props"] or "{}")
                        except Exception:
                            props = {"legacy_props": str(row["props"] or "")}
                        migration_key = f"{MIGRATION_MARKER}:event:{int(row['id'])}"
                        cur.execute(
                            """
                            INSERT INTO growth_events (
                                ts, user_id, name, props, migration_key
                            ) VALUES (COALESCE(%s, NOW()), %s, %s, %s, %s)
                            ON CONFLICT (migration_key) DO NOTHING
                            RETURNING id
                            """,
                            (
                                row["ts"], row["user_id"], str(row["name"]),
                                Jsonb(props), migration_key,
                            ),
                        )
                        if cur.fetchone():
                            counts["events"] += 1

                marker_value = json.dumps(counts, ensure_ascii=False, sort_keys=True)
                cur.execute(
                    """
                    INSERT INTO growth_migration_meta (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
                    """,
                    (MIGRATION_MARKER, marker_value),
                )
            target.commit()
    finally:
        source.close()

    return {**counts, "status": "migrated"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=os.getenv("SQLITE_DB_PATH", "jobs.db"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dsn = os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    result = migrate(args.sqlite, dsn, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
