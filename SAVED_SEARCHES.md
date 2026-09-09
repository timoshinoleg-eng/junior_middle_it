# Saved Searches

## Purpose

Saved Searches turn realtime alerts from a broad bot preference into an explicit
retention subscription. A user can keep several concrete search definitions and
receive only new vacancies matching at least one of them.

## Entry points

### From Resume Match

After a Resume Match result, `🔔 Сохранить такой поиск` builds a search from:

- the vacancy category;
- skills that were actually found in both the resume and vacancy;
- Junior/Middle-only policy.

The resume text itself is never stored in the search.

### From profile

`/profile` shows current saved searches and offers
`💾 Сохранить текущий профиль`. The resulting search uses the existing category,
skill, salary and seniority filters.

Deleting a search is also available from `/profile`.

## Alert semantics

The existing single realtime-alert stage remains authoritative. No additional
worker or cron is introduced.

For an alert subscriber:

1. if enabled Saved Searches exist, a new vacancy must match at least one search;
2. otherwise the existing profile matcher is used;
3. `REALTIME_ALERTS_MAX` still limits messages per crawl cycle;
4. saving a search explicitly enables alerts;
5. `/alerts off` still disables delivery even when searches remain stored.

This keeps user control predictable and prevents duplicate notifications.

## Persistence

When PostgreSQL growth persistence is configured, Saved Searches are stored in
`growth_saved_searches`. Local/dev fallback uses SQLite `saved_searches`.

A fingerprint prevents repeated saves of the same search from creating duplicate
subscriptions. PostgreSQL and SQLite both scope deletion by `(user_id, id)`.

## Metrics

Events:

- `saved_search_created` with `search_id` and source (`profile` or
  `resume_match`);
- `saved_search_deleted`;
- `realtime_alert_sent` now includes `saved_search: true|false`.

No resume text or personal document content enters these events.

## Next step

Semantic similarity (`pgvector`) should be layered behind this product contract
only after enough real Saved Searches exist to justify it. Structured category,
skill and salary filters remain authoritative even when vector ranking is added.
