# v6.4 — Multi-track channels

## What
Route vacancies to specialty Telegram channels by category instead of one firehose.

## Config — Вариант A (рекомендуемый)

Одна строка `CHANNEL_ROUTES`. `CHANNEL_ID` = main + fallback (`*` в routes).

```env
ENABLE_MULTI_TRACK=true
CHANNEL_ID=@junior_all
CHANNEL_ROUTES=development,devops:@junior_dev;qa:@junior_qa;data:@junior_data;design,pm:@junior_design;*:@junior_all
MULTI_TRACK_MIRROR_MAIN=false
MULTI_TRACK_POST_DELAY=1.0
```

| Правило | Куда |
|---------|------|
| development, devops | `@junior_dev` |
| qa | `@junior_qa` |
| data | `@junior_data` |
| design, pm | `@junior_design` |
| `*` (остальное) | `@junior_all` (= `CHANNEL_ID`) |

Вариант B (shortcuts `CHANNEL_ID_DEV`…) — только если `CHANNEL_ROUTES` пуст.

## Behavior
- Job category → matching track channel(s)
- Unmatched → `*` route or `CHANNEL_ID`
- Digests / salary magnet still go to main `CHANNEL_ID`
- Dedup hash is global (one post registration even if multi-channel)

## Commands
- `/tracks` — show routing map

## Setup checklist
1. Create 2–4 channels, add bot as admin with post rights
2. Set routes in `.env`
3. `ENABLE_MULTI_TRACK=true`
4. Restart bot; check logs for route table
