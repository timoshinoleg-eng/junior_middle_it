"""Cohort-correct product metrics for the P7 production runtime.

Definitions:
- acquisition cohort: users whose first-ever ``start`` is in the reporting window;
- activated: acquisition-cohort users with a high-intent event during D0
  (first 24 hours after first start);
- D1/D7 retention: activated-D0 users old enough to reach the requested day,
  with any later product event in the corresponding 24-hour window.

Retention windows are shifted back by 1/7 days so a ``7d`` report has a full
7-day mature cohort instead of an almost-empty D7 denominator.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict


HIGH_INTENT_EVENTS = [
    "save_job",
    "job_full",
    "personal_digest_sent",
    "realtime_alert_sent",
    "first_value_delivered",
]


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def compute_growth_stats(store, days: int = 7) -> Dict[str, Any]:
    days = max(1, min(int(days), 90))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    conn = store._ensure_conn()

    def unique_event(name: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM growth_events
                WHERE name=%s AND ts >= %s AND user_id IS NOT NULL
                """,
                (name, cutoff),
            )
            row = cur.fetchone()
        return int((row or [0])[0] or 0)

    def event_count(name: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM growth_events WHERE name=%s AND ts >= %s",
                (name, cutoff),
            )
            row = cur.fetchone()
        return int((row or [0])[0] or 0)

    with conn.cursor() as cur:
        # First-start cohort keeps setup/activation numerators strict subsets of
        # acquisition and prevents old users from inflating conversion.
        cur.execute(
            """
            WITH first_start AS (
                SELECT user_id, MIN(ts) AS first_ts
                FROM growth_events
                WHERE name='start' AND user_id IS NOT NULL
                GROUP BY user_id
            ), cohort AS (
                SELECT user_id, first_ts
                FROM first_start
                WHERE first_ts >= %s AND first_ts <= %s
            )
            SELECT
                COUNT(*) AS starts,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM growth_events e
                    WHERE e.user_id=c.user_id
                      AND e.name='setup_done'
                      AND e.ts >= c.first_ts
                      AND e.ts <= %s
                )) AS setup_done,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM growth_events e
                    WHERE e.user_id=c.user_id
                      AND e.name = ANY(%s)
                      AND e.ts >= c.first_ts
                      AND e.ts < c.first_ts + INTERVAL '1 day'
                      AND e.ts <= %s
                )) AS activated
            FROM cohort c
            """,
            (cutoff, now, now, HIGH_INTENT_EVENTS, now),
        )
        funnel = cur.fetchone() or (0, 0, 0)
        starts, setup_done, activated = [int(v or 0) for v in funnel]

        cur.execute("SELECT COUNT(*) FROM growth_user_settings")
        users_total = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE digest_enabled=TRUE")
        digest_subscribers = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE alerts_enabled=TRUE")
        alert_subscribers = int((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COUNT(*) FROM growth_user_settings WHERE premium_unlocked=TRUE")
        premium_users = int((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COUNT(*) FROM growth_referrals WHERE created_at >= %s", (cutoff,))
        referrals = int((cur.fetchone() or [0])[0] or 0)
        cur.execute(
            """
            SELECT referrer_id, COUNT(*) AS c
            FROM growth_referrals
            WHERE created_at >= %s
            GROUP BY referrer_id
            ORDER BY c DESC
            LIMIT 10
            """,
            (cutoff,),
        )
        top_referrers = [(int(r[0]), int(r[1])) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT DATE(ts), COUNT(DISTINCT user_id)
            FROM growth_events
            WHERE ts >= %s AND user_id IS NOT NULL
            GROUP BY DATE(ts)
            ORDER BY DATE(ts) DESC
            LIMIT 14
            """,
            (cutoff,),
        )
        users_by_day = [(str(r[0]), int(r[1])) for r in cur.fetchall()]

        # Attribute acquisition by the payload on the user's first-ever start,
        # not by every subsequent /start invocation.
        cur.execute(
            """
            WITH ranked_start AS (
                SELECT user_id, ts, props,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts, id) AS rn
                FROM growth_events
                WHERE name='start' AND user_id IS NOT NULL
            )
            SELECT COALESCE(props->>'payload', ''), COUNT(*)
            FROM ranked_start
            WHERE rn=1 AND ts >= %s AND ts <= %s
            GROUP BY COALESCE(props->>'payload', '')
            ORDER BY COUNT(*) DESC
            LIMIT 12
            """,
            (cutoff, now),
        )
        acquisition = [(str(r[0] or "direct"), int(r[1])) for r in cur.fetchall()]

        # D1/D7 denominators contain only users activated on D0. Cohort windows
        # are shifted back so every included user is mature for that milestone.
        d1_floor = cutoff - timedelta(days=1)
        d1_ceiling = now - timedelta(days=1)
        d7_floor = cutoff - timedelta(days=7)
        d7_ceiling = now - timedelta(days=7)
        cur.execute(
            """
            WITH first_start AS (
                SELECT user_id, MIN(ts) AS first_ts
                FROM growth_events
                WHERE name='start' AND user_id IS NOT NULL
                GROUP BY user_id
            ), activated_d0 AS (
                SELECT f.user_id, f.first_ts
                FROM first_start f
                WHERE EXISTS (
                    SELECT 1 FROM growth_events a
                    WHERE a.user_id=f.user_id
                      AND a.name = ANY(%s)
                      AND a.ts >= f.first_ts
                      AND a.ts < f.first_ts + INTERVAL '1 day'
                )
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE first_ts >= %s AND first_ts < %s
                ) AS d1_cohort,
                COUNT(*) FILTER (
                    WHERE first_ts >= %s AND first_ts < %s
                      AND EXISTS (
                          SELECT 1 FROM growth_events e
                          WHERE e.user_id=a.user_id
                            AND e.ts >= a.first_ts + INTERVAL '1 day'
                            AND e.ts <  a.first_ts + INTERVAL '2 days'
                      )
                ) AS d1_retained,
                COUNT(*) FILTER (
                    WHERE first_ts >= %s AND first_ts < %s
                ) AS d7_cohort,
                COUNT(*) FILTER (
                    WHERE first_ts >= %s AND first_ts < %s
                      AND EXISTS (
                          SELECT 1 FROM growth_events e
                          WHERE e.user_id=a.user_id
                            AND e.ts >= a.first_ts + INTERVAL '7 days'
                            AND e.ts <  a.first_ts + INTERVAL '8 days'
                      )
                ) AS d7_retained
            FROM activated_d0 a
            """,
            (
                HIGH_INTENT_EVENTS,
                d1_floor, d1_ceiling,
                d1_floor, d1_ceiling,
                d7_floor, d7_ceiling,
                d7_floor, d7_ceiling,
            ),
        )
        retention = cur.fetchone() or (0, 0, 0, 0)

    d1_cohort, d1_retained, d7_cohort, d7_retained = [int(v or 0) for v in retention]
    return {
        "days": days,
        "starts": starts,
        "setup_done": setup_done,
        "saves": unique_event("save_job"),
        "referrals": referrals,
        "ref_views": unique_event("ref_view"),
        "personal_digests": unique_event("personal_digest_sent"),
        "channel_digests": event_count("daily_digest_posted"),
        "realtime_alerts": unique_event("realtime_alert_sent"),
        "activated_users": activated,
        "setup_conversion_pct": _pct(setup_done, starts),
        "activation_conversion_pct": _pct(activated, starts),
        "d1_cohort": d1_cohort,
        "d1_retained": d1_retained,
        "d7_cohort": d7_cohort,
        "d7_retained": d7_retained,
        "d1_retention_pct": _pct(d1_retained, d1_cohort),
        "d7_retention_pct": _pct(d7_retained, d7_cohort),
        "acquisition": acquisition,
        "top_referrers": top_referrers,
        "events_by_day": users_by_day,
        "users_total": users_total,
        "digest_subscribers": digest_subscribers,
        "alert_subscribers": alert_subscribers,
        "premium_users": premium_users,
    }
