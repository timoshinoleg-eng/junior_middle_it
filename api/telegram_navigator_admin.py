"""Publish (idempotently) and pin the channel navigator post.

Manual, secured endpoint: same contract as ``api/telegram_branding_admin.py``
(``Authorization: Bearer $CRON_SECRET``, production only, safe to re-run —
an existing navigator pin is detected by its title and left untouched).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))

from channel_navigator import DEFAULT_CHANNEL, NAVIGATOR_TITLE, build_navigator_post
from serverless_health import cron_authorized


def _bot_username() -> str:
    return (
        (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
        or (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    )


def _telegram_call(token: str, method: str, payload: dict | None = None):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=25,
    )
    data = response.json()
    if not response.ok or not data.get("ok"):
        description = str(data.get("description") or f"HTTP {response.status_code}")
        raise RuntimeError(f"{method}: {description[:180]}")
    return data.get("result")


def _try_call(token: str, method: str, payload: dict | None = None):
    try:
        return True, _telegram_call(token, method, payload), ""
    except RuntimeError as exc:
        return False, None, str(exc)[:220]


def _apply_navigator() -> dict:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    channel_id = (os.getenv("CHANNEL_ID") or "").strip() or DEFAULT_CHANNEL
    if not token:
        raise RuntimeError("telegram_token_missing")

    username = _bot_username()
    text, reply_markup = build_navigator_post(username, channel_id)

    chat_ok, chat, chat_error = _try_call(token, "getChat", {"chat_id": channel_id})
    chat = chat or {}
    pinned = chat.get("pinned_message") or {}
    pinned_text = str(pinned.get("text") or pinned.get("caption") or "")
    message_id = pinned.get("message_id") if pinned_text.startswith(NAVIGATOR_TITLE) else None

    post_error = ""
    pin_error = ""
    posted = False
    if message_id is None:
        payload = {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup.get("inline_keyboard"):
            payload["reply_markup"] = reply_markup
        post_ok, sent, post_error = _try_call(token, "sendMessage", payload)
        if post_ok and isinstance(sent, dict):
            posted = True
            message_id = sent.get("message_id")
        else:
            message_id = None

    pinned_ok = False
    if message_id is not None and not pinned_text.startswith(NAVIGATOR_TITLE):
        pin_ok, _, pin_error = _try_call(
            token, "pinChatMessage", {"chat_id": channel_id, "message_id": message_id}
        )
        pinned_ok = bool(pin_ok)

    verified_chat_ok, verified_chat, verify_chat_error = _try_call(
        token, "getChat", {"chat_id": channel_id}
    )
    verified_chat = verified_chat or {}
    verified_pinned = verified_chat.get("pinned_message") or {}

    result = {
        "navigator_post_ok": bool(
            verified_chat_ok
            and str(verified_pinned.get("text") or "").startswith(NAVIGATOR_TITLE)
        ),
        "pinned_message_id": verified_pinned.get("message_id"),
        "posted_new": posted,
        "pin_post_status": (
            "existing" if (not posted and pinned_text.startswith(NAVIGATOR_TITLE))
            else ("pinned" if pinned_ok else "failed")
        ),
        "bot_username_set": bool(username),
        "channel_error": chat_error or verify_chat_error,
        "post_error": post_error,
        "pin_error": pin_error,
    }
    result["automated_ok"] = all([
        result["navigator_post_ok"],
        result["bot_username_set"],
        not result["post_error"],
        not result["pin_error"],
    ])
    return result


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send_json(405, {"ok": False, "error": "method_not_allowed"})

    def do_POST(self):
        if not cron_authorized(self.headers.get("authorization", "")):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            result = _apply_navigator()
        except RuntimeError as exc:
            error = str(exc)
            self._send_json(409 if error == "not_production" else 503, {"ok": False, "error": error})
            return
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send_json(200 if result.get("automated_ok") else 503, {"ok": bool(result.get("automated_ok")), **result})

    def log_message(self, fmt, *args):
        pass
