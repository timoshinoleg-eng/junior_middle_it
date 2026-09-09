# P7 production status — 2026-09-09

This document records only independently verified production facts. It contains no credentials.

## Supabase

Project: `junior-middle-it-growth` (`uucjptyqfsdzqsftgiro`, Frankfurt).

Live verification completed successfully on 2026-09-09:

- all six `public.growth_*` tables exist;
- RLS is enabled on all six tables;
- `anon` and `authenticated` have no table grants on growth data;
- transactional write/read smoke passed for user settings, referrals, events, job payloads and saved searches;
- all synthetic smoke writes were rolled back and follow-up queries confirmed no test rows remained;
- self-referral, negative salary and duplicate event migration-key constraints reject invalid writes;
- all six P7 tables currently contain zero production rows.

The repeatable verification is committed as `supabase/verify_p7.sql`.

Supabase advisors currently report only informational findings:

- security: RLS enabled with no policies on the six backend-only growth tables; this is intentional because public roles have no grants;
- performance: new indexes are currently unused, expected while the tables have no production traffic.

## Vercel

Production identifiers:

- project id: `prj_269y0sGpFXze7o7MqeDIfLakL5nR`
- team id: `team_szHwcCa6CdtVuEvDGWkvvbZ1`
- team scope: `olegs-projects-bfc4e11a`
- canonical domain: `https://junior-middle-it.vercel.app`

Routing code and Vercel preview deployments are green after PR #18. Production diagnostics added in PR #19 prove the canonical domain is still serving stale production traffic: `/`, `/robots.txt`, `/api/health`, `/api/cron` and direct `/api/index?...` probes all return the same old cron unauthorized response. This rules out a path-specific rewrite collision.

PR #20 added bounded self-repair to Production Smoke. It can explicitly deploy current `main` to the known Vercel project when an appropriately scoped CI token is available. The live workflow verified that no such token is currently configured in GitHub Actions.

The connected Vercel integration independently returns HTTP 403 for this team/project and states that scope `olegs-projects-bfc4e11a` must be re-authenticated. Therefore the remaining Vercel problem is an external authorization / production-traffic target blocker, not an application routing-code blocker.

## Next production sequence after Vercel scope access is restored

1. Inspect the current deployments for `prj_269y0sGpFXze7o7MqeDIfLakL5nR` under `team_szHwcCa6CdtVuEvDGWkvvbZ1`.
2. Promote/deploy current `main` to production.
3. Run Production Smoke until the canonical root, robots, health and cron endpoints serve the current router contract.
4. Activate the existing Supabase PostgreSQL durable-growth connection in the Vercel production environment.
5. Verify health reports durable growth ready.
6. Run one normal collector cycle and verify `growth_job_payloads` begins receiving production rows.
7. Continue the accepted P7 rollout with the interactive runtime decision and final end-to-end smoke.
