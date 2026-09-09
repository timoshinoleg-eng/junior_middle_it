# Resume Match MVP

## Product goal

Turn each vacancy into a stronger utility/acquisition surface: the user can check
how well their existing resume text matches one concrete role before applying.

The feature is inspired by the product pattern used by open-source resume/JD
matching tools, but the implementation here is intentionally small and local to
the existing bot.

## User flow

1. Open a vacancy card.
2. Tap `📄 Проверить резюме`.
3. Paste resume text in one Telegram message.
4. Receive:
   - heuristic match score;
   - explicit skills found in both texts;
   - skills explicitly present in the vacancy but not found in the resume;
   - keyword overlap;
   - practical tailoring suggestions.
5. Optionally save a search based on the matched skills (`🔔 Сохранить такой поиск`) or refine the profile.

The saved-search suggestion contains only the vacancy category and skills that
were found in both texts. It never contains the resume body or extracted
personal details. Saving it explicitly enables realtime alerts for that search.

## Privacy contract

Resume text is held only in the in-memory conversation update while the match is
calculated. It is removed from `resume_sessions` immediately after the result.

It is **not** written to:

- SQLite;
- PostgreSQL;
- analytics events;
- job payload cache;
- saved searches;
- logs by the Resume Match code.

The completion event contains only:

- vacancy hash;
- score;
- number of required skills;
- number of matched skills.

## Scoring contract

This is not an official ATS score and the bot says so explicitly.

The v1 score combines:

- explicit technology/skill coverage: up to 75 points;
- conservative keyword overlap: up to 20 points;
- target-role overlap: up to 5 points.

The matcher never marks a technology as present unless one of its explicit
aliases is found in the resume text. A missing-skill recommendation is only
created for a technology that was explicitly found in the vacancy text.

Users are explicitly told not to add skills they do not actually have.

## Durable vacancy context

Vacancy buttons can outlive a Render deploy. When PostgreSQL growth persistence
is configured, `production_bot.DatabaseConnection` mirrors serialized vacancy
payloads into `growth_job_payloads` for 45 days. Resume Match therefore remains
usable from older channel cards after the local SQLite cache has been replaced.

Without PostgreSQL the existing SQLite payload cache remains the fallback.

## Deliberate MVP limits

- paste text only; PDF/DOCX ingestion is a later step;
- no LLM calls;
- no embeddings/vector database;
- no automatic resume rewriting;
- no claim that a high score guarantees an interview;
- no storage of resumes by default.

These limits keep the feature cheap, auditable, safe to release, and suitable
for measuring whether Resume Match actually increases activation/retention
before expanding it.
