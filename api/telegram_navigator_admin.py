"""Publish (idempotently) and pin the channel navigator post.

Manual, secured endpoint: same contract as ``api/telegram_branding_admin.py``
(``Authorization: Bearer $CRON_SECRET``, production only, safe to re-run —
an existing navigator pin is detected by its title and left untouched).

Only stdlib is imported at module level: every third-party / repo-level
dependency is imported lazily inside the handlers, so a bundling problem
surfaces as a readable JSON payload instead of an opaque
FUNCTION_INVOCATION_FAILED.
"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))


def _bot_username() -> str:
    return (
        (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
        or (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    )


def _telegram_call(token: str, method: str, payload: dict | None = None):
    import requests

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


def _load_navigator_state(channel_id: str) -> tuple:
    """Return (store, state, error). ``store`` is ``None`` when Postgres is out."""
    from growth_store import GrowthStoreUnavailable, PostgresGrowthStore

    dsn = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        return None, {}, "growth_dsn_missing"
    try:
        store = PostgresGrowthStore(dsn, all_categories=[])
    except GrowthStoreUnavailable as exc:
        return None, {}, f"growth_store_unavailable: {exc}"[:200]
    key = f"navigator:{channel_id}"
    try:
        return store, store.get_channel_state(key), ""
    except Exception as exc:
        store.close()
        return None, {}, f"channel_state_read_failed: {type(exc).__name__}"


def _save_navigator_state(store, channel_id: str, state: dict) -> str:
    try:
        store.save_channel_state(f"navigator:{channel_id}", state)
        return ""
    except Exception as exc:
        return f"channel_state_write_failed: {type(exc).__name__}"


def _is_navigator_pin(pinned: dict, title: str) -> bool:
    return str((pinned or {}).get("text") or (pinned or {}).get("caption") or "").startswith(title)


def _apply_navigator() -> dict:
    from channel_navigator import DEFAULT_CHANNEL, NAVIGATOR_TITLE, build_navigator_post

    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    channel_id = (os.getenv("CHANNEL_ID") or "").strip() or DEFAULT_CHANNEL
    if not token:
        raise RuntimeError("telegram_token_missing")

    username = _bot_username()

    # 0) Durability gate: without Postgres state we cannot guarantee
    #    idempotency, and a failed pin would duplicate the post on retry.
    store, state, state_error = _load_navigator_state(channel_id)
    if store is None:
        raise RuntimeError(f"navigator_state_unavailable: {state_error}")

    # 1) Capability gate: pin BEFORE any posting. A bot without the
    #    "pin messages" right must never publish (otherwise every retry
    #    appends another copy of the post to the channel).
    me_ok, me, me_error = _try_call(token, "getMe")
    bot_id = (me or {}).get("id") if me_ok else None
    member_ok, member, member_error = _try_call(
        token, "getChatMember", {"chat_id": channel_id, "user_id": bot_id} if bot_id else {"chat_id": channel_id}
    )
    member = member or {}
    can_pin = bool(member.get("can_pin_message")) or member.get("status") == "creator"
    if not can_pin:
        store.close()
        return {
            "ok": False,
            "automated_ok": False,
            "navigator_post_ok": False,
            "posted_new": False,
            "post_message_id": None,
            "pin_post_status": "skipped",
            "bot_username_set": bool(username),
            "can_pin_message": False,
            "post_error": "",
            "pin_error": member_error or me_error or "bot lacks pin right in channel",
            "state_error": state_error,
        }

    text, reply_markup = build_navigator_post(username, channel_id)

    # 2) If a navigator post is already pinned — done.
    chat_ok, chat, chat_error = _try_call(token, "getChat", {"chat_id": channel_id})
    chat = chat or {}
    pinned = chat.get("pinned_message") or {}
    stored_id = state.get("post_message_id")
    stored_id = int(stored_id) if stored_id else None

    posted = False
    post_error = ""
    pin_error = ""
    message_id = None
    status = "existing"

    if _is_navigator_pin(pinned, NAVIGATOR_TITLE):
        message_id = pinned.get("message_id")
        status = "existing"
    else:
        # 3) Try to re-pin the post we already know about (no new send).
        if stored_id is not None:
            pin_ok, _, pin_error = _try_call(
                token, "pinChatMessage", {"chat_id": channel_id, "message_id": stored_id}
            )
            if pin_ok:
                message_id = stored_id
                status = "repinned"
            elif "not found" in (pin_error or "").lower():
                stored_id = None  # message was deleted; fall through to repost
                state.pop("post_message_id", None)
                pin_error = ""
        # 4) Post a fresh copy only when nothing reusable exists.
        if message_id is None and stored_id is None:
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
                status = "posted"
                state["post_message_id"] = message_id
                save_error = _save_navigator_state(store, channel_id, state)
                if save_error:
                    post_error = (post_error + "; " + save_error).strip("; ")
            else:
                message_id = None

        # 5) Pin the freshly posted message (reused messages were pinned in step 3).
        if posted and message_id is not None:
            pin_ok, _, pin_error = _try_call(
                token, "pinChatMessage", {"chat_id": channel_id, "message_id": message_id}
            )
            status = "pinned" if pin_ok else "pin_failed"

    verified_chat_ok, verified_chat, verify_chat_error = _try_call(
        token, "getChat", {"chat_id": channel_id}
    )
    verified_chat = verified_chat or {}
    verified_pinned = verified_chat.get("pinned_message") or {}
    navigator_ok = bool(
        verified_chat_ok and _is_navigator_pin(verified_pinned, NAVIGATOR_TITLE)
    )

    result = {
        "navigator_post_ok": navigator_ok,
        "pinned_message_id": verified_pinned.get("message_id") if navigator_ok else None,
        "post_message_id": message_id,
        "posted_new": posted,
        "pin_post_status": status,
        "bot_username_set": bool(username),
        "can_pin_message": True,
        "channel_error": chat_error or verify_chat_error,
        "post_error": post_error,
        "pin_error": pin_error,
        "state_error": state_error,
    }
    result["automated_ok"] = all([
        result["navigator_post_ok"],
        result["bot_username_set"],
        result["can_pin_message"],
        not result["post_error"],
        not result["pin_error"],
    ])
    store.close()
    return result


def _import_probe() -> dict:
    """Diagnostic: try each lazy dependency and capture tracebacks."""
    probes: dict = {"sys_path_head": sys.path[:6], "cwd": os.getcwd()}
    try:
        here = Path(__file__).resolve()
        probes["file"] = str(here)
        probes["dir_listing"] = sorted(p.name for p in here.parent.iterdir())
        root = here.parents[1]
        probes["root_listing"] = sorted(p.name for p in root.iterdir())
    except Exception:
        probes["dir_error"] = traceback.format_exc()[-600:]
    for name in ("requests", "serverless_health", "channel_navigator"):
        try:
            module = __import__(name)
            probes[name] = getattr(module, "__file__", "builtin")
        except Exception:
            probes[f"{name}_error"] = traceback.format_exc()[-900:]
    return probes


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        if "selftest" in parse_qs(urlparse(self.path).query):
            self._send_json(200, {"ok": True, "probes": _import_probe()})
            return
        self._send_json(405, {"ok": False, "error": "method_not_allowed"})

    def do_POST(self):
        try:
            from serverless_health import cron_authorized
        except Exception:
            self._send_json(500, {
                "ok": False,
                "error": "serverless_health_import_failed",
                "detail": traceback.format_exc()[-1200:],
            })
            return
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
        status = 200 if result.get("automated_ok") else 503
        if result.get("can_pin_message") is False:
            status = 409  # permanent: retrying cannot fix missing channel rights
        self._send_json(status, {"ok": bool(result.get("automated_ok")), **result})

    def log_message(self, fmt, *args):
        pass
