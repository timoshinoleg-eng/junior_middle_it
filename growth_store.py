"""Durable PostgreSQL growth-state storage for junior_middle_it.

Vacancy ingestion/dedup remains on the existing SQLite cache for now. State
that must survive deploys (profiles, referrals and growth events) lives here
when GROWTH_DATABASE_URL/DATABASE_URL is configured.
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
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None
    PSYCOPG_AVAILABLE = False


class GrowthStoreUnavailable(RuntimeError):
    pass


class PostgresGrowthStore:
    """PostgreSQL store for profiles, referrals and product events."""

    def __init__(self, dsn: str, all_categories: Iterable[str]):
        if not dsn:
            raise GrowthStoreUnavailable("PostgreSQL DSN is empty")
        if not PSYCOPG_AVAILABLE:
            raise GrowthStoreUnavailable("psycopg is not installed")
        self.dsn = dsn
        self.all_categories = list(all_categories)
        self.conn = None
        self._connect()
        self._initialize()

    def _connect(self) -> None:
        try:
            # Vercel is a serverless runtime, so production should use Supabase's
            # Supavisor transaction pooler (port 6543). Transaction pooling does
            # not support prepared statements; psycopg otherwise starts preparing
            # frequently executed statements automatically after a small number of
            # executions. Disable auto-prepare so a warm function remains safe when
            # the underlying server connection changes between transactions.
            self.conn = psycopg.connect(
                self.dsn,
                autocommit=True,
                connect_timeout=8,
                prepare_threshold=None,
            )
        except Exception as exc:  # pragma: no cover - external service
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
        with self._ensure_conn().cursor() as cur:
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
        with self._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT enabled_categories, hide_senior, min_salary_filter,
                       skills, digest_enabled, onboarding_done, alerts_enabled,
                       premium_unlocked
                FROM growth_user_settings WHERE user_id = %s
                """,
                (int(user_id),),
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
        with self._ensure_conn().cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_user_settings (
                    user_id, enabled_categories, hide_senior, min_salary_filter,
                    skills, digest_enabled, onboarding_done, alerts_enabled,
                    premium_unlocked, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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
                    int(user_id), cats_str, bool(current.get("hide_senior", True)),
                    int(current.get("min_salary_filter") or 0), str(current.get("skills") or ""),
                    bool(current.get("digest_enabled")), bool(current.get("onboarding_done")),
                    bool(current.get("alerts_enabled")), bool(current.get("premium_unlocked")),
                ),
            )
        return True

    def _list_subscribers(self, column: str) -> List[int]:
        if column not in {"digest_enabled", "alerts_enabled"}:
            raise ValueError("unsupported subscriber column")
        with self._ensure_conn().cursor() as cur:
            cur.execute(f"SELECT user_id FROM growth_user_settings WHERE {column} = TRUE")
            return [int(row[0]) for row in cur.fetchall()]

    def list_digest_subscribers(self) -> List[int]:
        return self._list_subscribers("digest_enabled")

    def list_alert_subscribers(self) -> List[int]:
        return self._list_subscribers("alerts_enabled")

    def log_event(self, user_id: Optional[int], name: str, props: Optional[Dict] = None) -> None:
        with self._ensure_conn().cursor() as cur:
            cur.execute(
                "INSERT INTO growth_events (user_id, name, props) VALUES (%s, %s, %s)",
                (user_id, str(name), Jsonb(props or {})),
            )

    def register_referral(self, user_id: int, referrer_id: int) -> bool:
        if not user_id or not referrer_id or int(user_id) == int(referrer_id):
            return False
        with self._ensure_conn().cursor() as cur:
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
        with self._ensure_conn().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM growth_referrals WHERE referrer_id = %s",
                (int(referrer_id),),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def growth_stats(self, days: int = 7) -> Dict[str, Any]:
        """Unique-user acquisition/activation funnel with eligible D1/D7 cohorts."""
        days = max(1, int(days))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
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

        def event_count(name: str) -> int:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM growth_events WHERE name = %s AND ts >= %s",
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
        high_intent = [
            "save_job", "job_full", "personal_digest_sent",
            "realtime_alert_sent", "first_value_delivered",
        ]

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM growth_events
                WHERE ts >= %s AND user_id IS NOT NULL AND name = ANY(%s)
                """,
                (cutoff, high_intent),
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

            cur.execute("SELECT COUNT(*) FROM growth_referrals WHERE created_at >= %s", (cutoff,))
            referrals = int((cur.fetchone() or [0])[0])
            cur.execute(
                """
                SELECT referrer_id, COUNT(*) AS c FROM growth_referrals
                WHERE created_at >= %s GROUP BY referrer_id ORDER BY c DESC LIMIT 10
                """,
                (cutoff,),
            )
            top_referrers = [(int(r[0]), int(r[1])) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT DATE(ts), COUNT(DISTINCT user_id) FROM growth_events
                WHERE ts >= %s AND user_id IS NOT NULL
                GROUP BY DATE(ts) ORDER BY DATE(ts) DESC LIMIT 14
                """,
                (cutoff,),
            )
            users_by_day = [(str(r[0]), int(r[1])) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT COALESCE(props->>'payload', ''), COUNT(DISTINCT user_id)
                FROM growth_events
                WHERE name='start' AND ts >= %s AND user_id IS NOT NULL
                GROUP BY COALESCE(props->>'payload', '')
                ORDER BY COUNT(DISTINCT user_id) DESC LIMIT 12
                """,
                (cutoff,),
            )
            acquisition = [(str(r[0] or "direct"), int(r[1])) for r in cur.fetchall()]

            # Only users old enough to reach the milestone belong in its denominator.
            cur.execute(
                """
                WITH first_start AS (
                    SELECT user_id, MIN(ts) AS first_ts FROM growth_events
                    WHERE name='start' AND user_id IS NOT NULL GROUP BY user_id
                )
                SELECT
                    COUNT(*) FILTER (
                        WHERE first_ts >= %s AND first_ts <= %s
                    ) AS d1_cohort,
                    COUNT(*) FILTER (
                        WHERE first_ts >= %s AND first_ts <= %s AND EXISTS (
                            SELECT 1 FROM growth_events e
                            WHERE e.user_id=f.user_id
                              AND e.ts >= f.first_ts + INTERVAL '1 day'
                              AND e.ts <  f.first_ts + INTERVAL '2 days'
                        )
                    ) AS d1_retained,
                    COUNT(*) FILTER (
                        WHERE first_ts >= %s AND first_ts <= %s
                    ) AS d7_cohort,
                    COUNT(*) FILTER (
                        WHERE first_ts >= %s AND first_ts <= %s AND EXISTS (
                            SELECT 1 FROM growth_events e
                            WHERE e.user_id=f.user_id
                              AND e.ts >= f.first_ts + INTERVAL '7 days'
                              AND e.ts <  f.first_ts + INTERVAL '8 days'
                        )
                    ) AS d7_retained
                FROM first_start f
                """,
                (
                    cutoff, now - timedelta(days=1),
                    cutoff, now - timedelta(days=1),
                    cutoff, now - timedelta(days=7),
                    cutoff, now - timedelta(days=7),
                ),
            )
            retention = cur.fetchone() or (0, 0, 0, 0)

        d1_cohort, d1_retained, d7_cohort, d7_retained = [int(v or 0) for v in retention]

        def pct(n: int, d: int) -> float:
            return round(100.0 * n / d, 1) if d else 0.0

        return {
            "days": days,
            "starts": starts,
            "setup_done": setup_done,
            "saves": saves,
            "referrals": referrals,
            "ref_views": ref_views,
            "personal_digests": personal_digests,
            "channel_digests": event_count("daily_digest_posted"),
            "realtime_alerts": realtime_alerts,
            "activated_users": activated,
            "setup_conversion_pct": pct(setup_done, starts),
            "activation_conversion_pct": pct(activated, starts),
            "d1_cohort": d1_cohort,
            "d1_retained": d1_retained,
            "d7_cohort": d7_cohort,
            "d7_retained": d7_retained,
            "d1_retention_pct": pct(d1_retained, d1_cohort),
            "d7_retention_pct": pct(d7_retained, d7_cohort),
            "acquisition": acquisition,
            "top_referrers": top_referrers,
            "events_by_day": users_by_day,
            "users_total": users_total,
            "digest_subscribers": digest_subscribers,
            "alert_subscribers": alert_subscribers,
            "premium_users": premium_users,
        }
