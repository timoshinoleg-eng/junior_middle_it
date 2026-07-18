# v6.6 — Publish quality + source auto-skip

## Source auto-skip

- `SourceHealthRegistry` tracks **fail streak** on hard errors
- After `SOURCE_FAIL_SKIP` consecutive failures (default **3**), source is skipped next cycles until a success
- Admin `/sources` shows fail×N and ⏭️ skip markers
- Set `SOURCE_FAIL_SKIP=0` to disable

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
