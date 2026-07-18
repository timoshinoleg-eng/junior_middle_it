# v6.4 — Multi-track channels

## What
Route vacancies to specialty Telegram channels by category instead of one firehose.

## Config
```env
ENABLE_MULTI_TRACK=true
CHANNEL_ID=@junior_all
CHANNEL_ROUTES=development,devops:@junior_dev;qa:@junior_qa;data:@junior_data;design,pm:@junior_design;*:@junior_all

# or shortcuts:
CHANNEL_ID_DEV=@junior_dev
CHANNEL_ID_QA=@junior_qa
CHANNEL_ID_DATA=@junior_data
CHANNEL_ID_DESIGN=@junior_design

MULTI_TRACK_MIRROR_MAIN=false   # also post specialty jobs to CHANNEL_ID
MULTI_TRACK_POST_DELAY=1.0
```

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
