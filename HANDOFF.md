# Handoff — junior_middle_it (post v6.6 merge)

**Date:** 2026-07-18  
**Repo:** https://github.com/timoshinoleg-eng/junior_middle_it  
**Main tip:** `d68c900` — Merge PR #6 (stack v6.1–v6.6 + review fix)  
**Local:** branch `main` fast-forwarded to `origin/main`

## Status

| Item | State |
|------|--------|
| PR #1–#6 | **MERGED** into `main` |
| Open PRs | none (growth stack closed) |
| Unit tests | 30 OK (`test_growth_utils` + `test_job_sources_extra`) |
| Live sources | 4dayweek / Muse / RemoteOK multi-cat / Working Nomads / RSS OK (set `LIVE_SOURCE_TESTS=1`) |

## What shipped (v6.1 → v6.6)

1. **v6.1** — fuzzy dedup (RapidFuzz), referrals, FloodWait, diversify, salary min gate, digests  
2. **v6.2** — `/setup` `/profile` `/digest`, personal DM, CHANNEL_TRACKS, invite CTA  
3. **v6.3** — `/stats_growth`, salary magnet, premium refs, realtime alerts, dedup retention  
4. **v6.4** — multi-track `CHANNEL_ROUTES` (Variant A), `/tracks`  
5. **v6.5** — free sources: 4dayweek, The Muse, RemoteOK multi-cat, Working Nomads, WWR+Himalayas RSS; ATS expand; `/sources` health  
6. **v6.6** — `compute_publish_score`, title/company normalize, `SOURCE_FAIL_SKIP` with **cooldown+probe** (not permanent)

**Review fix (on main):** auto-skip no longer permanent; Windows signal handlers try/except; `datetime` UTC aware.

## Key files

```
channel_bot.py          # pipeline, bot cmds, safe_fetch, diversify
growth_utils.py         # fuzzy, salary, multi-track, scores, normalize
job_sources_extra.py    # free APIs/RSS + SourceHealthRegistry
message_formatter.py
telegram_job_parser.py
test_growth_utils.py
test_job_sources_extra.py
CHANGELOG_v6.1.md … v6.6.md
.env.example.txt
README.md               # updated for v6.6
```

## Deploy env (minimum + growth)

```env
TELEGRAM_BOT_TOKEN=
CHANNEL_ID=
BOT_USERNAME=

# v6.4 multi-track (vars not sent yet by owner — optional)
ENABLE_MULTI_TRACK=false
# CHANNEL_ROUTES=development,qa,devops:@dev;data:@data;design,pm:@design;*:@main
# MULTI_TRACK_MIRROR_MAIN=false

# v6.5 sources
ENABLE_EXTRA_SOURCES=true
ENABLE_RSS_SOURCES=true

# v6.6 quality
SOURCE_FAIL_SKIP=3
ENABLE_SOURCE_DIVERSIFY=true
MAX_POSTS_PER_CYCLE=40
```

Admin after crawl: `/sources`, `/status`, `/stats_growth`, `/tracks`.

## Known constraints / follow-ups

1. **CHANNEL_ROUTES** — owner will send specialty channel IDs later; leave multi-track off until then.  
2. **Keyed APIs** (Reed/Jooble/FindWork/SuperJob/Adzuna) — only if keys appear in env; not required.  
3. **Source health is in-memory** — resets on process restart (serverless cold start clears streaks).  
4. **Overlap** RemoteOK full + multi-cat / Himalayas API + RSS — OK, hash/fuzzy dedup.  
5. **PTB LGPLv3** — noted earlier; keep awareness for commercial redistribute.  
6. **ROI next** — deploy prod + white traffic + metrics > more sources.

## Next session ideas (not started)

- Wire `CHANNEL_ROUTES` when channel vars arrive  
- Persist source health to SQLite (survive cold start)  
- Mini App / resume match (mentioned in v6.1 changelog backlog)  
- Vercel deploy verify after merge (preview was green on #1)  
- Optional: prune dead ATS board slugs after first week of `/sources` data

## Commands for new session

```bash
git clone https://github.com/timoshinoleg-eng/junior_middle_it.git
cd junior_middle_it
git checkout main && git pull
python -m unittest test_job_sources_extra test_growth_utils -v
# live optional:
# LIVE_SOURCE_TESTS=1 python -m unittest test_job_sources_extra.LiveSourceTests -v
```

## Review summary (this session)

| Severity | Finding | Action |
|----------|---------|--------|
| **High** | Auto-skip permanent after 3 fails (no probe) | **Fixed** — N-cycle cooldown then probe |
| Medium | `add_signal_handler` crashes Windows loop | **Fixed** — try/except |
| Low | `datetime.utcnow` deprecated | **Fixed** → timezone.utc |
| Info | Dual RemoteOK/Himalayas paths | Accept — dedup |
| Info | RSS XML dirty namespaces | Mitigated via strip prefixes |
| Info | PRs #1–#5 stacked under #6 | Merged via #6 tip |

## Do not

- Black-hat growth / private channel scrape  
- Merge without re-pull if new work started off stale main  
- Enable multi-track without real specialty channel IDs  
