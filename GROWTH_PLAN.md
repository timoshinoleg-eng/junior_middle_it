# Growth plan — junior_middle_it

Updated: 2026-09-09

## Product diagnosis

The repository already has broad vacancy coverage, editorial quality gates,
profiles, favorites, daily/personal digests, real-time alerts, referrals and
local event logging. The next growth constraint is not source count. It is the
closed-loop user journey:

`shared vacancy / content magnet -> bot start -> first useful match -> opt-in -> return -> share/referral`

The main priorities are therefore acquisition attribution, activation,
retention, durable analytics and stronger utility magnets.

## North-star and funnel

North-star: **weekly activated job seekers** — unique users who have a profile
and perform at least one high-intent action (open/save/digest/alert) in the
week.

Track unique users, not raw event counts:

1. Acquisition: `start` grouped by payload (`share_*`, `ref_*`, campaign tags).
2. Activation: `setup_done / unique_start` and first useful action within 24h.
3. Value: job opens, saves, digest matches, alerts, resume checks.
4. Retention: D1 / D7 / D30 return among activated users.
5. Virality: accepted referrals per active referrer and share-attributed starts.
6. Quality guardrails: hide-category rate, stale-link rate, zero-match profiles,
   blocked-bot rate and notification opt-out rate.

## P0 — reliability and acquisition loop

- [x] Replace the current inline-mode share button with Telegram's official
  share URL so sharing works without an `InlineQueryHandler`.
- [x] Add `start=share_<job_hash>` attribution to shared vacancy copy.
- [x] Add regression tests around share URLs and formatter compatibility.
- [x] Add GitHub Actions CI for Python 3.12/3.13 and the existing policy suite.
- [ ] Replace the misleading referral reward. Current copy promises Senior jobs,
  while the publication pipeline intentionally excludes senior vacancies.
  Reward with real value instead: earlier alerts, larger digests, saved searches
  and extra resume checks.
- [ ] Make the first `/start` screen action-oriented: 2-3 buttons, not a command
  manual. Primary CTA: "Подобрать вакансии за 30 сек".
- [ ] After onboarding, immediately send the first matched set instead of asking
  the user to type `/digest now`.

## P1 — durable user state and measurable retention

The current interactive bot stores users, referrals and events in SQLite. On an
ephemeral deployment this makes growth data and rewards vulnerable to restart or
redeploy. Move growth state first; job-source refactors can wait.

Recommended target: PostgreSQL.

Tables:
- users / preferences
- referrals
- events
- saved_jobs
- job_payloads
- notification_delivery
- saved_searches

Requirements:
- idempotent migrations;
- SQLite migration/import path;
- unique-user funnel queries;
- retention cohorts;
- campaign/start-payload dimension;
- delivery failures and bot-blocked state;
- backups and health checks.

## P2 — utility magnets that can acquire subscribers

### Resume-to-job match

Build a lightweight "Проверить резюме под вакансию" flow inspired by
`srbhr/Resume-Matcher` (Apache-2.0). Start without mandatory LLM calls:

1. User chooses a posted vacancy.
2. Uploads/pastes resume text.
3. Extract required skills/keywords from the vacancy.
4. Return match %, missing skills/keywords, concrete checklist and a CTA to save
   a search for similar vacancies.
5. Do not store the resume by default.

This is a stronger shareable lead magnet than an undifferentiated vacancy feed.
Later, borrow UX ideas from `AmruthPillai/Reactive-Resume` (MIT) for resume
editing/export, but do not fork the entire application for v1.

### Search and saved searches

Phase 1: PostgreSQL full-text + filters.
Phase 2: `pgvector` for semantic matching while keeping structured filters in
Postgres. If search becomes a standalone product surface, `Meilisearch` CE is a
strong alternative for typo-tolerant full-text/hybrid search. `Qdrant` is an
alternative when vector search becomes large/independent enough to justify a
separate service.

Saved-search examples:
- Junior Python worldwide, salary known
- QA automation, Europe
- Data/AI entry-level, no strict country restriction

Each saved search becomes an explicit retention subscription.

## P3 — content loops for channel growth

Keep raw vacancy posts, but add recurring content people forward:

- weekly Junior/Middle salary pulse;
- "10 вакансий без 5+ years requirement";
- "5 worldwide roles";
- stack-specific weekly collections;
- vacancy freshness/competition signals;
- resume checklist tied to a real vacancy;
- simple interview-prep cards for the most common stacks.

Every magnet must have one measurable CTA with a campaign payload, e.g.
`?start=salary_week_37`, rather than a generic bot link.

## P4 — analytics and experimentation

Do not add analytics software before event durability is fixed.

Options:
- **PostHog**: strongest fit for product funnels, retention, paths and lifecycle.
- **Umami**: lighter MIT option for a future landing page / Mini App traffic and
  simpler product analytics.
- **GrowthBook**: add after baseline metrics for controlled onboarding/message
  experiments and gradual rollouts.
- **Formbricks**: useful for targeted micro-surveys, but its core is AGPL; use as
  a service/integration or accept AGPL obligations before copying/modifying code.

First experiments:
1. `/start`: profile-first vs first-vacancies-first.
2. onboarding: 4-step vs one-tap role presets.
3. notification choice: digest default vs alerts default.
4. vacancy card: salary/location-first vs title/company-first.
5. referral reward framing.

## P5 — source coverage only where it creates differentiated inventory

The repository already has many APIs/ATS/RSS/TG sources. Add sources only when
metrics show an inventory gap.

Useful OSS building blocks:

- `speedyapply/JobSpy` (MIT): adapters for major job boards. Treat rate limits,
  ToS and source reliability as first-class concerns; do not make it a P0
  dependency.
- `DIYgod/RSSHub`: broad feed adapters for sources with stable/public routes.
- `dgtlmoon/changedetection.io`: monitor selected company career pages that have
  no usable API/RSS; its own README explicitly lists career-page/job monitoring.
- `miniflux/v2` (Apache-2.0): lightweight feed ingestion/reference implementation
  if RSS orchestration becomes operationally expensive.

## OSS adoption matrix

| Project | Use here | Priority | Adoption mode |
|---|---|---:|---|
| srbhr/Resume-Matcher | resume/JD matching magnet | P2 | adapt Apache-2.0 concepts/code selectively |
| AmruthPillai/Reactive-Resume | resume UX/export patterns | P3 | reference/selective MIT reuse |
| pgvector/pgvector | semantic vacancy/profile match | P2 | dependency with PostgreSQL |
| meilisearch/meilisearch | search/saved-search surface | P2/P3 | optional service, CE/MIT |
| qdrant/qdrant | vector matching at larger scale | P3 | alternative service, Apache-2.0 |
| PostHog/posthog | funnel/retention analytics | P4 | hosted or OSS integration |
| umami-software/umami | lightweight web/Mini App analytics | P4 | optional MIT service |
| growthbook/growthbook | A/B tests and flags | P4 | integration after baseline |
| novuhq/novu | notification workflows/preferences | P4 | only if multi-channel complexity grows |
| speedyapply/JobSpy | additional job-board adapters | P5 | selective MIT integration |
| DIYgod/RSSHub | RSS adapter coverage | P5 | selective integration |
| dgtlmoon/changedetection.io | long-tail career-page watches | P5 | external service/integration |
| miniflux/v2 | robust feed ingestion patterns | P5 | reference/service |
| formbricks/formbricks | targeted feedback | P4 | integration; respect AGPL |

## Release order

1. P0 share fix + CI.
2. Persistence migration design and implementation.
3. Funnel metrics with unique users and retention.
4. Honest referral reward + activation-first onboarding.
5. Resume matcher MVP.
6. Saved searches / semantic matching.
7. Content magnets with campaign attribution.
8. Only then A/B tooling and incremental source expansion.

## Explicit non-goals

- no private-channel scraping or black-hat subscriber acquisition;
- no mass unsolicited DMs;
- no fake referral rewards;
- no adding sources purely to increase a README counter;
- no LLM/GPU dependency for the first resume-match MVP;
- no major rewrite of the existing editorial pipeline while growth metrics are
  still unreliable.
