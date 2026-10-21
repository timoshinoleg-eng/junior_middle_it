"""Glue of the Copilot Mini App API.

The module holds the parts of the Mini App backend that are worth testing on
their own: the payload the Mini App renders, the error contract and the Bot
API call that delivers a generated CV into the chat. The HTTP handler itself
lives in ``api/copilot.py``.

Privacy: the API answers with the data of one signed user only. Resume files
are parsed in memory and never written to the database, the logs or the file
names of the storage.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from copilot import fit as copilot_fit
from copilot import grade_audit as copilot_grade
from copilot import hr_link as copilot_hr
from copilot.credit_operations import DELIVERY_REJECTED, DELIVERY_UNKNOWN

MAX_BODY_BYTES = 4_000_000
MAX_RESUME_BYTES = 2_500_000
MAX_CAPTION_CHARS = 1_000
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW_MINUTES = 10
BOT_API_TIMEOUT = 30

__all__ = [
    "DELIVERY_REJECTED",
    "DELIVERY_UNKNOWN",
    "WebappError",
]


class WebappError(Exception):
    """An error with the HTTP status and the message the Mini App shows.

    ``delivery`` tells whether a failed Telegram call is known to have
    rejected the document or only left the outcome open. A rejected document
    can be paid back, an open outcome cannot: Telegram may have stored it even
    though the answer was lost.
    """

    def __init__(self, status: int, code: str, message: str, *, delivery: Optional[str] = None):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.delivery = delivery

    def payload(self) -> Dict:
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
        }


def job_brief(job: Dict) -> Dict:
    """Public fields of a vacancy for the Mini App."""
    job = job or {}
    return {
        "hash": str(job.get("hash") or ""),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "url": str(job.get("url") or ""),
    }


def analysis_payload(job: Dict, profile: Optional[Dict]) -> Dict:
    """The deterministic analysis of one vacancy for one profile."""
    report = copilot_fit.compute_fit(profile, job)
    audit = copilot_grade.audit_grade(job)
    handle = copilot_hr.extract_hr_handle(job)
    return {
        "job": job_brief(job),
        "fit": report.as_dict(),
        "grade_audit": audit.as_dict(),
        "hr": {
            "handle": str(handle or ""),
            "url": f"https://t.me/{handle}" if handle else "",
        },
    }


def _safe_filename(name: str) -> str:
    """File name safe for a multipart header."""
    raw = str(name or "cv.pdf").strip() or "cv.pdf"
    cleaned = "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in raw)
    return (cleaned or "cv.pdf")[:64]


def build_multipart(
    fields: Dict[str, str],
    filename: str,
    content: bytes,
    content_type: str = "application/pdf",
) -> Tuple[bytes, str]:
    """Build a multipart/form-data body for one file."""
    seed = hashlib.sha256(
        (filename or "cv.pdf").encode("utf-8") + bytes(content[:64] or b"")
    ).hexdigest()[:24]
    boundary = "----copilot" + seed
    parts: List[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; '
            f'filename="{_safe_filename(filename)}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(bytes(content))
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _bot_api_call(token: str, method: str, payload: Dict) -> Dict:
    """One Bot API call, without logging the token or the payload."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=BOT_API_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        rejected = 400 <= int(exc.code) < 500
        raise WebappError(
            502,
            "telegram_error",
            "Telegram не принял файл. Попробуй позже.",
            delivery=DELIVERY_REJECTED if rejected else DELIVERY_UNKNOWN,
        ) from exc
    except Exception as exc:
        raise WebappError(
            502,
            "telegram_error",
            "Telegram недоступен. Попробуй позже.",
            delivery=DELIVERY_UNKNOWN,
        ) from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise WebappError(
            502,
            "telegram_error",
            "Telegram не принял файл. Попробуй позже.",
            delivery=DELIVERY_REJECTED,
        )
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def send_document_to_chat(
    token: str,
    chat_id: int,
    filename: str,
    content: bytes,
    caption: str = "",
) -> Dict:
    """Upload a document into a chat and return its Telegram file id."""
    if not bytes(content or b""):
        raise WebappError(400, "empty_document", "Файл пустой.")
    body, content_type = build_multipart(
        {
            "chat_id": str(int(chat_id)),
            "caption": str(caption or "")[:MAX_CAPTION_CHARS],
        },
        filename,
        content,
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=BOT_API_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # Telegram answered. A 4xx means the document was rejected, a 5xx and
        # every lost answer leave the outcome open.
        rejected = 400 <= int(exc.code) < 500
        raise WebappError(
            502,
            "telegram_error",
            "Telegram не принял файл. Попробуй позже.",
            delivery=DELIVERY_REJECTED if rejected else DELIVERY_UNKNOWN,
        ) from exc
    except Exception as exc:
        raise WebappError(
            502,
            "telegram_error",
            "Telegram недоступен. Попробуй позже.",
            delivery=DELIVERY_UNKNOWN,
        ) from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise WebappError(
            502,
            "telegram_error",
            "Telegram не принял файл. Попробуй позже.",
            delivery=DELIVERY_REJECTED,
        )
    result = data.get("result") or {}
    document = result.get("document") or {}
    return {
        "file_id": str(document.get("file_id") or ""),
        "message_id": int(result.get("message_id") or 0),
    }


def resend_document(token: str, chat_id: int, file_id: str, caption: str = "") -> Dict:
    """Send an already uploaded document again, without a new upload."""
    if not str(file_id or "").strip():
        raise WebappError(400, "missing_file_id", "Файл не найден в кэше.")
    result = _bot_api_call(
        token,
        "sendDocument",
        {
            "chat_id": int(chat_id),
            "document": str(file_id),
            "caption": str(caption or "")[:MAX_CAPTION_CHARS],
        },
    )
    document = result.get("document") or {}
    return {
        "file_id": str(document.get("file_id") or file_id),
        "message_id": int(result.get("message_id") or 0),
    }
