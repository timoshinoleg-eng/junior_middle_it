# Durable growth state rollout

`channel_bot_v2.py` keeps the existing vacancy ingestion/dedup cache in SQLite
but stores state that must survive deploys in PostgreSQL:

- user profiles and notification opt-ins;
- referrals and referral rewards;
- product analytics events used for acquisition, activation and retention.

This is intentionally incremental. It avoids a high-risk rewrite of the mature
vacancy pipeline while removing the data-loss risk for subscriber growth.

## Production environment

Set a PostgreSQL connection string:

```env
GROWTH_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
REQUIRE_DURABLE_GROWTH=true
ADMIN_USER_ID=123456789
```

`DATABASE_URL` is accepted as a fallback when the platform already exposes that
name. `GROWTH_DATABASE_URL` wins when both are present.

`REQUIRE_DURABLE_GROWTH=true` is recommended in production. With it enabled, the
bot refuses to start when PostgreSQL is unavailable instead of silently falling
back to ephemeral SQLite growth state.

The Render health response exposes only safe presence/status flags:
`GROWTH_DATABASE`, `REQUIRE_DURABLE_GROWTH`, and `ADMIN_USER_ID`; it never
returns credentials.

## Migrating the existing jobs.db growth state

Back up `jobs.db`, deploy PostgreSQL credentials, then run once from an
environment that has access to the existing SQLite file:

```bash
GROWTH_DATABASE_URL='postgresql://...' \
python migrate_growth_to_postgres.py --sqlite jobs.db
```

The migration copies:

- `user_settings` -> `growth_user_settings`;
- `referrals` -> `growth_referrals`;
- `events` -> `growth_events`.

It executes in one PostgreSQL transaction and writes the marker
`sqlite_growth_v1` to `growth_migration_meta`. A completed migration is a no-op
on subsequent runs. `--force` is available only for an intentional re-import.

After checking `/health` and `/stats_growth`, enable:

```env
REQUIRE_DURABLE_GROWTH=true
```

## Growth metrics after PostgreSQL activation

`/stats_growth` switches from raw command counts to a unique-user funnel:

- unique starts;
- setup conversion;
- activated users;
- unique savers/digest/alert users;
- accepted referrals;
- acquisition grouped by `/start` payload;
- D1/D7 return cohorts;
- top referrers.

This establishes the baseline required before introducing PostHog/GrowthBook or
optimizing acquisition campaigns.

## Referral reward contract

The old copy promised Senior vacancies after enough referrals, but the
editorial policy intentionally excludes Senior roles. The reward is now honest:
meeting the referral threshold unlocks a larger personal digest by
`REF_REWARD_DIGEST_BONUS` entries. Later releases may add saved-search and resume
match allowances, but must not advertise unavailable inventory.

## Admin security

Admin commands now fail closed. If `ADMIN_USER_ID` is absent, they are disabled
for everyone rather than becoming public. `/tracks` is also treated as an admin
command because it exposes internal routing configuration.
