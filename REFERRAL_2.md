# Referral 2.0

## Goal

Increase subscriber acquisition without optimizing for empty `/start` events.
The referral loop distinguishes **accepted** referrals from **activated**
referrals and ranks users by activation quality.

## User experience

`/ref` shows:

- personal referral deep link;
- total accepted referrals;
- total activated referrals and activation rate;
- accepted/activated referrals in the last 7 days;
- personal weekly position;
- privacy-safe top weekly scores (counts only, no Telegram IDs/usernames);
- progress toward the existing digest bonus;
- one-tap Telegram share button.

## What counts as activated

An invitee counts toward leaderboard quality only when a `setup_done` event
exists **after** the referral was created.

This deliberately avoids ranking by raw clicks or starts.

## Existing-user anti-abuse

Referral attribution is accepted only when the invitee has no prior product
history:

- no previous growth event;
- no existing user settings/profile row.

The check happens before the new `/start` event is written, so a genuinely new
user arriving through `ref_<user_id>` is still attributed normally.

Self-referrals and duplicate first-touch attribution remain rejected by the
existing referral store.

## Reward compatibility

The already shipped digest bonus threshold still uses accepted referrals. This
avoids silently taking a reward away from existing users.

The leaderboard, weekly rank, and new quality analytics use activated referrals.
If abuse appears in real metrics, reward eligibility can later move to activated
referrals as a separate product migration.

## Privacy

The user-facing leaderboard never publishes Telegram IDs, usernames, names, or
stable pseudonyms. It displays only aggregate score positions such as:

`#1: 7 · #2: 5 · #3: 2`

The user's own rank is visible only in their private `/ref` response.

## Analytics

Admin `/stats_growth` adds a Referral quality block:

- referrals accepted in the selected period;
- referred users activated;
- referral activation rate;
- number of referrers;
- number of referrers with at least one activated invitee;
- highest activated-referral score.

These metrics should be evaluated together with the existing unique-start,
setup, Resume Match, Saved Search and retention metrics.
