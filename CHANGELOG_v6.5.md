# v6.5 — New job sources + source health

## New free parsers (no API key)

| Source | Method | Notes |
|--------|--------|--------|
| **4dayweek.io** | REST `GET /api/v2/jobs` | remote + junior/mid filters; salary cents→USD |
| **The Muse** | REST public jobs | Flexible/Remote + software categories |
| **RemoteOK Dev** | multi category JSON | dev, backend, fullstack, design, devops, data |
| **Working Nomads** | `GET /api/exposed_jobs/` | public list, no key |
| **RSS boards** | WWR programming + design/devops/product + Himalayas Atom | generic RSS/Atom parser |

Dead feed removed: `nodejsjobslist.com` (DNS NXDOMAIN).

## ATS expansion (defaults)

Larger Greenhouse / Lever / Ashby company lists (remote-first tech). Override via env.

## Ops

- **Source health** recorded every crawl (`safe_fetch_with_retry`)
- **`/sources`** (admin) — last-cycle fetched counts + errors + configured list

## Env

```env
ENABLE_EXTRA_SOURCES=true
ENABLE_RSS_SOURCES=true
```

## Live tests

```bash
# Windows PowerShell
$env:LIVE_SOURCE_TESTS='1'
python -m unittest test_job_sources_extra -v
```

## How to add another free source

1. Implement `fetch_foo() -> List[Dict]` in `job_sources_extra.py` with normalized fields:
   `title, company, description, url, salary, location, published, employment_type, source, tags`
2. Append `(fetch_foo, "Foo")` in `get_extra_fetchers()`.
3. If 100% remote board → add `"Foo"` to `REMOTE_ONLY_SOURCES` in `channel_bot.py`.
4. Optional unit + `LIVE_SOURCE_TESTS=1` case in `test_job_sources_extra.py`.

## Fair use

- 4dayweek: ≤60 req/min, credit https://4dayweek.io
- RemoteOK: credit Remote OK, follow-link back per their API terms
- Working Nomads / Muse / WWR / Himalayas: public endpoints, polite UA + rate limits via crawl delays
- Respect robots/ToS of RSS hosts; no private channel scrape
