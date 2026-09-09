# P7 durable writer: Vercel -> Supabase Edge

Production does not export the Supabase database password to Vercel.

- `GROWTH_PUBLIC_URL` points at the hosted `growth-proxy` Edge Function.
- Vercel sends a vacancy payload to `/job-payloads` before publishing its Telegram CTA.
- The request carries the already-configured `TELEGRAM_BOT_TOKEN` only in an HTTPS header.
- The Edge Function validates that token against Telegram `getMe` and requires the exact production bot username before performing the single `growth_job_payloads` upsert.
- Vercel never gets arbitrary SQL access or `SUPABASE_DB_URL`.
- Public landing reads use the unauthenticated read-only `/public-jobs` projection.

If the durable pre-save fails, the serverless publisher fails closed rather than publishing a Resume Match/share CTA whose payload cannot be recovered later.
