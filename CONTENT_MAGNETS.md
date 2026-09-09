# Content Magnets

## Goal

Grow the channel through content that is useful enough to forward, while making
every acquisition source measurable through Telegram `/start` payloads.

The first release deliberately reuses the existing weekly salary scheduler. It
does not add another cron, worker or publishing service.

## Weekly outputs

The existing weekly hook now publishes two posts:

1. **Salary pulse** — the current salary aggregation, now with an attributable
   CTA such as `?start=magnet_salary_2026w37`.
2. **Weekly picks** — up to seven recent Junior/Middle remote vacancies selected
   across different companies and categories, with a CTA such as
   `?start=magnet_picks_2026w37`.

The campaign identifier uses ISO year/week so every weekly edition is grouped
naturally in acquisition analytics while staying inside Telegram's deep-link
payload limits.

## Selection rules

Weekly picks are built only from vacancies that already passed the normal
publication pipeline. The magnet does not create a second editorial policy.

Ranking favours:

- known salary;
- Junior, then Middle roles;
- worldwide/remote availability;
- valid application URL;
- useful tags/stack context.

Diversity rules prevent one company or one category from dominating the first
selection. If those constraints would leave the post too short, remaining slots
are filled from the ranked list without duplicate vacancies.

## Durability

When PostgreSQL growth persistence is available, magnet generation reads recent
vacancy payloads from `growth_job_payloads`. This allows weekly content to
survive Render redeploys that replace the local SQLite filesystem.

SQLite `recent_jobs_for_digest` remains the local/dev fallback.

## Attribution

Existing `/start` analytics already store the raw payload. Therefore no new
tracking SDK is required for the first measurement cycle.

Compare unique starts for:

- `magnet_salary_*`;
- `magnet_picks_*`;
- `share_*`;
- referral payloads;
- direct starts.

The next content format should only be added after these cohorts show which
magnet actually produces activated users, not merely clicks.
