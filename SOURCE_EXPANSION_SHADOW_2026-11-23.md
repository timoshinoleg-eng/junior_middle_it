# Junior/Middle IT — sources shadow audit (2026-11-23)

Scope: repository timoshinoleg-eng/junior_middle_it, based on main 249d460.
This branch is **preparation only**. Do not merge, enable flags, invoke cron, post to
Telegram, or deploy during the 5–7-day production freeze.

## External read-only samples (2026-11-23)

Public feeds were read via an external fetch client, **not from Vercel**.
Successful remote fetches do **not** guarantee access from Vercel's serverless network.

| Feed | Items visible in sampled response | Notes |
|---|---:|---|
| Habr RSS remote/qid=1 (intern) | 10 | 10 explicit remote statements; source grade hashtag |
| Habr RSS remote/qid=3 (junior) | 17 | 17 explicit remote statements; Russian tech vacancies |
| Habr RSS remote/qid=4 (middle) | 50 | 50 explicit remote statements; includes non-IT titles |
| HeadHunter public RSS, Python + noExperience + remote | returned | Remote signal may appear only in the search URL; review separately |
| Ashby supabase, railway, clerk | returned | Already configured in channel_bot |
| Ashby posthog, n8n, render | returned | Candidates for later ATS shadow test; not added by this PR |
| Ashby mercury, deel | empty jobs | Do not add based on this sample |

The item counts above are **raw RSS sample sizes**, not eligible jobs, not
deduplicated jobs, and not a forecast for channel throughput.

## What this draft implements

- A specialized Habr RSS adapter for title/company/skill-tag/date/ruble salary extraction.
- Explicit per-item #intern, #junior or #middle evidence is required; query
  parameter qid alone is insufficient.
- The vacancy description must explicitly say remote; a remote search URL
  alone is insufficient.
- Canonical Habr vacancy links only; no Telegram user sessions or tokens.
- ENABLE_HABR_RSS=false by default; enabling must be a separate rollout step.
- Offline parser and editorial gate regression tests, included in the existing
  test_job_sources_extra CI suite.

## Release acceptance after freeze

1. Confirm authenticated /api/cron HTTP 200 and successful processing of
   production ingestion independently of this draft.
2. From a Vercel-like network, request all three Habr RSS feeds with no keys.
   Record actual HTTP status, content type, parse count, timeout, and rate limits.
3. Run a strictly read-only dry run through
   classify_job_level, is_suitable_job, apply_editorial_quality_gate,
   enrich_job_salary_fields, and canonical + fuzzy dedup against the last
   28 days of published jobs. Record per-source fetched, suitable, grade,
   quality-gate, fresh-unique, and reasons for rejection.
4. Confirm region restrictions from the original vacancy: "Можно удалённо"
   does **not** mean accessible from Russia or worldwide.
5. Enable on staging only and inspect Telegram preview cards before toggling
   production. Do not change the production limit or bypass quality gates.
6. Monitor duplicates, inappropriate positions, fetch errors and job freshness;
   rollback is ENABLE_HABR_RSS=false.

Unmeasured criteria must not be reported as PASS. No new source is enabled by
merging this branch alone. HH and ATS expansion remain separate follow-ups.
