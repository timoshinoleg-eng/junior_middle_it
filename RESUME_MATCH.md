# Resume Match

## Product goal

Turn each vacancy into a stronger utility/acquisition surface: a user can check
how well their existing resume matches one concrete role before applying, then
turn that result into a Saved Search / realtime retention subscription.

The matching pattern is inspired by open-source resume/JD tools, while this
implementation remains intentionally small, auditable and local to the existing
Telegram product.

## User flow

1. Open a vacancy card in the public channel or bot.
2. Tap `📄 Проверить резюме`.
3. With `BOT_USERNAME` configured, the button is a Telegram deep link:
   `?start=resume_<job_hash>`. This is important: a callback button on a public
   channel post does not establish a private bot conversation.
4. The bot opens Resume Match in the user's private chat and accepts one of:
   - pasted resume text;
   - PDF;
   - DOCX.
5. Receive:
   - heuristic match score;
   - explicit skills found in both texts;
   - skills explicitly present in the vacancy but not found in the resume;
   - keyword overlap;
   - practical tailoring suggestions.
6. Optionally save a search based on the matched skills (`🔔 Сохранить такой поиск`)
   or refine the profile.

The saved-search suggestion contains only the vacancy category and skills that
were found in both texts. It never contains the resume body or extracted
personal details. Saving it explicitly enables realtime alerts for that search.

## PDF / DOCX processing

Interactive Render runtime accepts PDF/DOCX through the existing conversation
handler. Vercel vacancy ingestion is unchanged.

Open-source parsers:

- `pypdf` for text PDFs;
- `python-docx` for DOCX.

Safety bounds:

- maximum upload: 5 MB;
- maximum PDF length: 25 pages;
- maximum DOCX uncompressed content: 25 MB;
- maximum DOCX ZIP members: 1000;
- extracted resume text is capped at 30,000 characters;
- DOCX structure and file signatures are checked instead of trusting Telegram
  MIME metadata or filename alone.

Image-only/scanned PDFs are deliberately **not OCRed** in this release. If a PDF
contains too little extractable text, the user is asked to upload DOCX or paste
text. This avoids adding a heavyweight OCR/runtime dependency for an unproven
product path.

## Privacy contract

Resume text and uploaded file bytes exist only in process memory while one
Resume Match is calculated. No temporary file is written by the Resume Match
path.

Resume content is **not** written to:

- SQLite;
- PostgreSQL;
- analytics events;
- job payload cache;
- saved searches;
- logs by Resume Match code.

File names are also not put into analytics or logs because they may contain a
person's name.

Completion analytics may contain only product-safe metadata such as:

- vacancy hash;
- score;
- number of required skills;
- number of matched skills;
- input type (`pdf`, `docx`; paste-text remains the existing path).

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
is configured, serialized vacancy payloads are mirrored into
`growth_job_payloads` for 45 days. Resume Match therefore remains usable from
older channel cards after the local SQLite cache has been replaced.

Without PostgreSQL the existing SQLite payload cache remains the fallback.

## Product measurement

Admin `/stats_growth` now has a second product-utility block with unique-user
metrics:

- Resume Match starts -> completions;
- PDF/DOCX users;
- average heuristic score;
- Resume Match -> Saved Search conversion;
- active Saved Searches / users;
- realtime alert recipients;
- content-magnet attributed starts and campaign payloads.

These metrics determine whether further investment in resume utilities is
justified.

## Current limits

- no OCR for scanned PDFs;
- no LLM calls;
- no embeddings/vector database;
- no automatic resume rewriting;
- no claim that a high score guarantees an interview;
- no storage of resumes by default.

The next expansion should be driven by measured completion and Saved Search
conversion, not by adding parsing/AI complexity speculatively.
