# Publication cadence — Junior Middle IT

## Normal schedule
Existing GitHub Actions Vercel Cron Trigger runs four times per UTC day: 00:00, 06:00, 12:00, 18:00 UTC. Actual starts may be delayed by GitHub Actions.

Vercel-only publication policy allows at most **7** distinct vacancies per scheduled cycle. Thus 4 scheduled cycles × 7 = **up to 28 scheduled vacancies per UTC day**. This is a ceiling, not a promise: low qualified supply yields fewer posts.

Manual GitHub workflow dispatches, retries and other deployment triggers add extra cycles, so 28 is **not** a transactional global daily quota. Do not claim a hard limit across all triggers. A strict cross-invocation quota would require an atomic UTC-day slot reservation in durable PostgreSQL with concurrent-execution and interrupted-send testing.

## Quality and safety
Only original, fresh and verifiably Junior/Middle roles pass publication. Preserve ATS/source attribution, accurate pay period and geo restrictions. Never invent absent facts.

Same-run canonical/fuzzy dedup and durable preselection probing remove previous postings before the per-cycle cap. Authoritative claim → sending → published idempotency remains unchanged. Source/track diversification maintains variety, and existing between-post delay of about three seconds paces each wave. No extra fetching waves or blind historical backfills.

## Verification for Issue #88
Per scheduled cycle review fetched, classified, selected_sources, posted, failed, pre_selection_duplicates, durable_duplicates, durable_probe_unavailable, durable_begin_failures and durable_finalize_failures. Compare them with the durable published rows in Supabase, grouped by UTC day and distinct hash. Do not substitute a green CI run for real Telegram success.

Product goal is 20–30 genuine distinct vacancies per UTC day for seven consecutive days, subject to available supply. If supply is lower, publish fewer and report the deficit rather than lowering standards.
