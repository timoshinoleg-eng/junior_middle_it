# Publication cadence — Junior Middle IT

## Normal schedule
Existing GitHub Actions Vercel Cron Trigger runs four times per UTC day: 00:00, 06:00, 12:00, 18:00 UTC. Actual starts may be delayed by GitHub Actions.

Vercel-only publication policy allows at most **10** distinct vacancies per scheduled cycle by default, with an override capped at 12. Four scheduled cycles allow **up to 40 by default or 48 at the maximum**. This is a ceiling, not a promise: low qualified supply yields fewer posts.

Manual GitHub workflow dispatches, retries and other deployment triggers add extra cycles, so these ceilings are **not** a transactional global daily quota. Do not claim a hard limit across all triggers. A strict cross-invocation quota would require an atomic UTC-day slot reservation in durable PostgreSQL with concurrent-execution and interrupted-send testing.

## Quality and safety
Only original, fresh and verifiably Junior/Middle roles pass publication. Preserve ATS/source attribution, accurate pay period and geo restrictions. Never invent absent facts.

Same-run canonical/fuzzy dedup and durable preselection probing remove previous postings before the per-cycle cap. Authoritative claim → sending → published idempotency remains unchanged. Source/track diversification maintains variety, and existing between-post delay of about three seconds paces each wave. No extra fetching waves or blind historical backfills.

## Verification for Issue #88
Per scheduled cycle review fetched, classified, selected_sources, posted, failed, pre_selection_duplicates, durable_duplicates, durable_probe_unavailable, durable_begin_failures and durable_finalize_failures. Compare them with the durable published rows in Supabase, grouped by UTC day and distinct hash. Do not substitute a green CI run for real Telegram success.

Product goal is 25–40 genuine distinct vacancies per UTC day for seven consecutive days, subject to available supply. If supply is lower, publish fewer and report the deficit rather than lowering standards.

`updatedAt` is a fallback date only; it can be later than publication. A valid publication date takes priority and remains subject to the freshness window. Increasing the burst cap does not create qualified supply or guarantee the target.

## Migration review (do not execute automatically)
`growth_channel_state` is accessed in `growth_store.py` using a direct PostgreSQL DSN. Repository search found no direct anon PostgREST table client, but the production DSN role and external/legacy clients have not been verified. **Deployment blocker:** confirm the runtime role retains table privileges and bypasses RLS or has an appropriate policy; inventory external clients before applying the migration. Apply through the operator's normal migration process and verify backend reads/writes. Rollback if needed: restore only previously recorded grants and RLS setting; avoid blindly granting public access. Record the prior table grants and RLS setting before applying.
