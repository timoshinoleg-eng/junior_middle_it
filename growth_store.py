"""Durable growth-state storage for junior_middle_it.

The vacancy ingestion pipeline can keep its existing SQLite cache/dedup tables,
while user state that must survive deploys (profiles, referrals, events) is
stored in PostgreSQL when GROWTH_DATABASE_URL/DATABASE_URL is configured.

This split is deliberate: it removes the highest-risk ephemeral state first
without rewriting the mature vacancy pipeline in one release.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.types.json import Jsonb

    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by fallback deployments
    psycopg = None
    Jsonb = None
    PSYCOPG_AVAILABLE = False


class GrowthStoreUnavailable(RuntimeError):
    """Raised when durable growth storage was requested but cannot start."""


class PostgresGrowthStore:
    """PostgreSQL store for user preferences, referrals and product events."""

    def __init__(self, dsn: str, all_categories: Iterable[str]):
        if not dsn:
            raise GrowthStoreUnavailable("PostgreSQL DSN is empty")
        if not PSYCOPG_AVAILABLE:
            raise GrowthStoreUnavailable(
                "psycopg is not installed; install requirements.txt before enabling durable growth"
            )
        self.dsn = dsn
        self.all_categories = list(all_categories)
        self.conn = None
        self._connect()
        self._initialize()

    def _connect(self) -> None:
        try:
            self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=8)
        except Exception as exc:  # pragma: no cover - depends on external DB
            raise GrowthStoreUnavailable(f"PostgreSQL connection failed: {exc}") from exc

    def _ensure_conn(self):
        if self.conn is None or getattr(self.conn, "closed", True):
            self._connect()
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass

    def _initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS growth_user_settings (
                user_id BIGINT PRIMARY KEY,
                enabled_categories TEXT NOT NULL DEFAULT '',
                hide_senior BOOLEAN NOT NULL DEFAULT TRUE,
                min_salary_filter INTEGER NOT NULL DEFAULT 0,
                skills TEXT NOT NULL DEFAULT '',
                digest_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                onboarding_done BOOLEAN NOT NULL DEFAULT FALSE,
                alerts_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                premium_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS growth_referrals (
                user_id BIGINT PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (user_id <> referrer_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS growth_events (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                user_id BIGINT,
                name TEXT NOT NULL,
                props JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_growth_events_name_ts ON growth_events(name, ts)",
            "CREATE INDEX IF NOT EXISTS idx_growth_events_user_ts ON growth_events(user_id, ts)",
            "CREATE INDEX IF NOT EXISTS idx_growth_referrals_referrer ON growth_referrals(referrer_id)",
        ]
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

    def _defaults(self) -> Dict[str, Any]:
        return {
            "enabled_categories": list(self.all_categories),
            "hide_senior": True,
            "min_salary_filter": 0,
            "skills": "",
            "digest_enabled": False,
            "onboarding_done": False,
            "alerts_enabled": False,
            "premium_unlocked": False,
        }

    def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT enabled_categories, hide_senior, min_salary_filter,
                       skills, digest_enabled, onboarding_done, alerts_enabled,
                       premium_unlocked
                FROM growth_user_settings WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return self._defaults()
        cats = [c for c in (row[0] or "").split(",") if c]
        return {
            "enabled_categories": cats or list(self.all_categories),
            "hide_senior": bool(row[1]),
            "min_salary_filter": int(row[2] or 0),
            "skills": row[3] or "",
            "digest_enabled": bool(row[4]),
            "onboarding_done": bool(row[5]),
            "alerts_enabled": bool(row[6]),
            "premium_unlocked": bool(row[7]),
        }

    def save_user_settings(self, user_id: int, settings: Dict[str, Any]) -> bool:
        current = self.get_user_settings(user_id)
        current.update({k: v for k, v in (settings or {}).items() if v is not None})
        cats = current.get("enabled_categories") or list(self.all_categories)
        cats_str = cats if isinstance(cats, str) else ",".join(str(c) for c in cats if c)
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_user_settings (
                    user_id, enabled_categories, hide_senior, min_salary_filter,
                    skills, digest_enabled, onboarding_done, alerts_enabled,
                    premium_unlocked, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    enabled_categories=EXCLUDED.enabled_categories,
                    hide_senior=EXCLUDED.hide_senior,
                    min_salary_filter=EXCLUDED.min_salary_filter,
                    skills=EXCLUDED.skills,
                    digest_enabled=EXCLUDED.digest_enabled,
                    onboarding_done=EXCLUDED.onboarding_done,
                    alerts_enabled=EXCLUDED.alerts_enabled,
                    premium_unlocked=EXCLUDED.premium_unlocked,
                    updated_at=NOW()
                """,
                (
                    int(user_id),
                    cats_str,
                    bool(current.get("hide_senior", True)),
                    int(current.get("min_salary_filter") or 0),
                    str(current.get("skills") or ""),
                    bool(current.get("digest_enabled")),
                    bool(current.get("onboarding_done")),
                    bool(current.get("alerts_enabled")),
                    bool(current.get("premium_unlocked")),
                ),
            )
        return True

    def list_digest_subscribers(self) -> List[int]:
        return self._list_subscribers("digest_enabled")

    def list_alert_subscribers(self) -> List[int]:
        return self._list_subscribers("alerts_enabled")

    def _list_subscribers(self, column: str) -> List[int]:
        if column not in {"digest_enabled", "alerts_enabled"}:
            raise ValueError("unsupported subscriber column")
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT user_id FROM growth_user_settings WHERE {column} = TRUE")
            rows = cur.fetchall()
        return [int(row[0]) for row in rows]

    def log_event(self, user_id: Optional[int], name: str, props: Optional[Dict] = None) -> None:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO growth_events (user_id, name, props) VALUES (%s, %s, %s)",
                (user_id, str(name), Jsonb(props or {})),
            )

    def register_referral(self, user_id: int, referrer_id: int) -> bool:
        if not user_id or not referrer_id or int(user_id) == int(referrer_id):
            return False
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_referrals (user_id, referrer_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING user_id
                """,
                (int(user_id), int(referrer_id)),
            )
            inserted = cur.fetchone()
        if not inserted:
            return False
        self.log_event(user_id, "referral_attributed", {"referrer_id": int(referrer_id)})
        self.log_event(referrer_id, "referral_invite_accepted", {"invitee_id": int(user_id)})
        return True

    def count_referrals(self, referrer_id: int) -> int:
        conn = self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM growth_referrals WHERE referrer_id = %s",
                (int(referrer_id),),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def growth_stats(self, days: int = 7) -> Dict[str, Any]:
        """Return unique-user funnel and basic retention, not raw command counts."""
        days = max(1, int(days))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        conn = self._ensure_conn()

        def unique_event(name: str) -> int:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT user_id) FROM growth_events
                    WHERE name = %s AND ts >= %s AND user_id IS NOT NULL
                    """,
                    (name, cutoff),
                )
                row = cur.fetchone()
            return int(row[0]) if row else 0

        starts = unique_event("start")
        setup_done = unique_event("setup_done")
        saves = unique_event("save_job")
        ref_views = unique_event("ref_view")
        personal_digests = unique_event("personal_digest_sent")
        realtime_alerts = unique_event("realtime_alert_sent")

        high_intent = (
            "save_job",
            "job_full",
            "personal_digest_sent",
            "realtime_alert_sent",
            "first_value_delivered",
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM growth_events
                WHERE ts >= %s AND user_id IS NOT NULL AND name = ANY(%s)
                """,
                (cutoff, list(high_intent)),
            )
            activated = int((cur.fetchone() or [0])[0])

            cur.execute("SELECT COUNT(*) FROM growth_user_settings")
            users_total = int((cur.fetchone() or [0])[0])
            cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE digest_enabled = TRUE")
            digest_subscribers = int((cur.fetchone() or [0])[0])
            cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE alerts_enabled = TRUE")
            alert_subscribers = int((cur.fetchone() or [0])[0])
            cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE premium_unlocked = TRUE")
            premium_users = int((cur.fetchone() or [0])[0])

            cur.execute(
                "SELECT COUNT(*) FROM growth_referrals WHERE created_at >= %s",
                (cutoff,),
            )
            referrals = int((cur.fetchone() or [0])[0])

            cur.execute(
                """
                SELECT referrer_id, COUNT(*) AS c
                FROM growth_referrals WHERE created_at >= %s
                GROUP BY referrer_id ORDER BY c DESC LIMIT 10
                """,
                (cutoff,),
            )
            top_referrers = [(int(r[0]), int(r[1])) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT DATE(ts) AS d, COUNT(DISTINCT user_id)
                FROM growth_events
                WHERE ts >= %s AND user_id IS NOT NULL
                GROUP BY DATE(ts) ORDER BY d DESC LIMIT 14
                """,
                (cutoff,),
            )
            users_by_day = [(str(r[0]), int(r[1])) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(props->>'payload', ''), COUNT(DISTINCT user_id)
                FROM growth_events
                WHERE name = 'start' AND ts >= %s AND user_id IS NOT NULL
                GROUP BY COALESCE(props->>'payload', '')
                ORDER BY COUNT(DISTINCT user_id) DESC LIMIT 12
                """,
                (cutoff,),
            )
            acquisition = [(str(r[0] or "direct"), int(r[1])) for r in cur.fetchall()]

            cur.execute(
                """
                WITH first_start AS (
                    SELECT user_id, MIN(ts) AS first_ts
                    FROM growth_events
                    WHERE name = 'start' AND user_id IS NOT NULL
                    GROUP BY user_id
                )
                SELECT
                    COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM growth_events e
                        WHERE e.user_id = f.user_id
                          AND e.ts >= f.first_ts + INTERVAL '1 day'
                          AND e.ts <  f.first_ts + INTERVAL '2 days'
                    )) AS d1,
                    COUNT(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM growth_events e
                        WHERE e.user_id = f.user_id
                          AND e.ts >= f.first_ts + INTERVAL '7 days'
                          AND e.ts <  f.first_ts + INTERVAL '8 days'
                    )) AS d7,
                    COUNT(*) AS cohort
                FROM first_start f
                WHERE f.first_ts >= %s
                """,
                (cutoff,),
            )
            retention_row = cur.fetchone() or (0, 0, 0)

        cohort = int(retention_row[2] or 0)
        d1 = int(retention_row[0] or 0)
        d7 = int(retention_row[1] or 0)
        pct = lambda n, d: round((100.0 * n / d), 1) if d else 0.0

        return {
            "days": days,
            "starts": starts,
            "setup_done": setup_done,
            "saves": saves,
            "referrals": referrals,
            "ref_views": ref_views,
            "personal_digests": personal_digests,
            "channel_digests": unique_event("daily_digest_posted"),
            "realtime_alerts": realtime_alerts,
            "activated_users": activated,
            "setup_conversion_pct": pct(setup_done, starts),
            "activation_conversion_pct": pct(activated, starts),
            "retention_cohort": cohort,
            "d1_retained": d1,
            "d7_retained": d7,
            "d1_retention_pct": pct(d1, cohort),
            "d7_retention_pct": pct(d7, cohort),
            "acquisition": acquisition,
            "top_referrers": top_referrers,
            "events_by_day": users_by_day,
            "users_total": users_total,
            "digest_subscribers": digest_subscribers,
            "alert_subscribers": alert_subscribers,
            "premium_users": premium_users,
        }
