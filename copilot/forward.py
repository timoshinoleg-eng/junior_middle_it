"""Forward-to-Analyze: turn a forwarded vacancy into a normal job payload.

The user forwards a vacancy post into the private chat and the bot answers with
the same deterministic analysis it gives for a channel post. Everything in this
module is pure: it only decides whether a message is a candidate, builds a job
payload and derives a stable hash.

Two guards protect the product:

* a command or a document is never a vacancy, so the setup flow and the resume
  upload keep working;
* a text that looks like a resume is refused, because the product never stores
  resume content in the database.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional

MIN_FORWARD_CHARS = 25
MAX_TEXT_CHARS = 4_000
HASH_CHARS = 40

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

# Texts that describe a candidate, not a vacancy. The product keeps resume
# content out of the database, so such messages are never analyzed or stored.
RESUME_SIGNALS = (
    "#резюме",
    "#resume",
    "#cv",
    "резюме",
    "curriculum vitae",
    "зарплатные ожидания",
    "salary expectations",
    "ожидания по зарплате",
    "формат работы:",
    "о себе:",
    "ищу работу",
    "в поиске работы",
    "open to work",
    "looking for a job",
    "looking for work",
)


def normalize_text(text: str) -> str:
    """Whitespace and case normalized text, used for the deduplication hash."""
    value = _ZERO_WIDTH_RE.sub("", str(text or ""))
    return _WS_RE.sub(" ", value).strip().lower()


def job_hash_for(text: str) -> str:
    """Stable hash of a forwarded text: the same post always gets one hash."""
    digest = hashlib.sha256(normalize_text(text).encode("utf-8"))
    return digest.hexdigest()[:HASH_CHARS]


def extract_url(text: str) -> str:
    """First http(s) link of a text, without trailing punctuation."""
    match = URL_RE.search(str(text or ""))
    if not match:
        return ""
    return match.group(0).rstrip(".,;:!?")


def looks_like_resume(text: str) -> bool:
    """Whether the text describes a candidate instead of a vacancy."""
    value = str(text or "").lower()
    return any(signal in value for signal in RESUME_SIGNALS)


def is_forward_candidate(
    text: str,
    *,
    forwarded: bool = False,
    has_document: bool = False,
) -> bool:
    """Whether the message may be analyzed as a forwarded vacancy.

    Commands and documents are never candidates: the first belongs to the
    command layer, the second to the resume upload flow.
    """
    if has_document:
        return False
    body = str(text or "").strip()
    if not body or body.startswith("/"):
        return False
    if len(body) < MIN_FORWARD_CHARS:
        return False
    if forwarded:
        return True
    return bool(URL_RE.search(body))


def build_job(text: str) -> Dict:
    """Build a job payload from a forwarded vacancy text."""
    body = str(text or "").strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    title = lines[0][:120] if lines else "вакансия"
    return {
        "hash": job_hash_for(body),
        "title": title,
        "company": "",
        "description": body[:MAX_TEXT_CHARS],
        "requirements": "",
        "tags": [],
        "url": extract_url(body),
        "source": "forwarded",
    }


def job_payload_from_row(row: Optional[Dict]) -> Optional[Dict]:
    """Rebuild a job payload from a stored forwarded job row."""
    if not row:
        return None
    text = str(row.get("text") or "")
    job_hash = str(row.get("job_hash") or "") or job_hash_for(text)
    return {
        "hash": job_hash,
        "title": str(row.get("title") or "вакансия"),
        "company": str(row.get("company") or ""),
        "description": text,
        "requirements": "",
        "tags": [],
        "url": str(row.get("url") or ""),
        "source": "forwarded",
    }
