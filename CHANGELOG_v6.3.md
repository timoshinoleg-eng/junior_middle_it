# v6.3 — Growth ops

## Features
1. **`/stats_growth [days]`** (admin) — funnel from `events`: starts, saves, refs, digests, alerts, top referrers
2. **Weekly salary magnet** — Monday (configurable) channel post with median min USD by category×level
3. **Soft ref rewards** — `REF_REWARD_THRESHOLD` invites → `premium_unlocked` (Senior in match + bigger digests)
4. **Dedup retention 28 days** — `DEDUP_RETENTION_DAYS` (was hard-coded 7)
5. **Realtime alerts** — `/alerts on` → up to N matching jobs DM after each crawl cycle

## Commands
- `/stats_growth` `/alerts on|off` (+ existing setup/digest/ref)

## Env
`DEDUP_RETENTION_DAYS`, `ENABLE_REALTIME_ALERTS`, `REALTIME_ALERTS_MAX`,
`REF_REWARD_THRESHOLD`, `REF_REWARD_DIGEST_BONUS`,
`ENABLE_WEEKLY_SALARY_REPORT`, `SALARY_REPORT_WEEKDAY`, `SALARY_REPORT_HOUR_UTC`,
`GROWTH_STATS_DAYS`
