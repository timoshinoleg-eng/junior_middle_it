# v6.6 — Publish quality + source auto-skip

## Source auto-skip

- `SourceHealthRegistry` tracks **fail streak** on hard errors
- After `SOURCE_FAIL_SKIP` consecutive failures (default **3**), source is skipped for N cycles, then **probed once** (no permanent skip)
- Success clears streak + cooldown
- Admin `/sources` shows fail×N, cooldown, ⏭️ markers
- Set `SOURCE_FAIL_SKIP=0` to disable
- Windows: signal handlers wrapped (no crash on ProactorEventLoop)

## Publish ranking

- `compute_publish_score()` — Junior/Middle bias, salary, URL, description length, tags, remote location
- `job_quality_score` now sorts by **(quality, recency)** inside diversify
- New sources added to `SOURCE_PUBLICATION_PRIORITY` (4dayweek, Muse, Working Nomads, RemoteOK Dev, RSS)

## Title/company normalize

- `normalize_job_title_company()` splits `Company: Role` (WWR RSS / generic source names)
- Applied before suitability + classification

## Env

```env
SOURCE_FAIL_SKIP=3
```
