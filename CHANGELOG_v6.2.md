# v6.2 — P1 personalization (1.1–1.5)

## Features
1. **Personal DM digest** — `/digest on|off|now`, daily JobQueue fan-out by profile
2. **Onboarding** — `/setup` wizard (categories → salary → skills → digest), `/profile`
3. **Channel tracks** — `CHANNEL_TRACKS` filters public channel categories
4. **Expand / compact / save** — `job_payloads` cache; expand/compact re-render MarkdownV2; save → `/favorites` without re-parse break
5. **Invite CTA** — button «🎁 Пригласить друзей» → `t.me/bot?start=invite` on every job keyboard

## Commands
- `/setup` `/profile` `/digest on|off|now` (plus existing `/ref` `/favorites` …)

## Env
`CHANNEL_TRACKS`, `ENABLE_PERSONAL_DIGEST`, `PERSONAL_DIGEST_MAX`, `PERSONAL_DIGEST_LOOKBACK_HOURS`
