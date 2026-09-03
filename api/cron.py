"""Vercel serverless entrypoint: /api/cron (authorized publication cycle) + /api/health.

v7 Stage 0 hardening:
- B02 fail-closed: without CRON_SECRET the endpoint returns 503 instead of
  running unprotected. The secret is accepted ONLY via the
  ``Authorization: Bearer`` header; the query-string form was removed because
  it leaked the secret into access logs.
- B04: error responses no longer embed tracebacks — stack traces go to Sentry
  and server logs only; clients receive a generic ``internal_error``.
"""
import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

from channel_bot import Config, collect_and_post_once, logger
from sentry_setup import init_sentry

# Initialize Sentry once per serverless instance (import-time).
init_sentry()


def authorize_cron_request(headers, secret: Optional[str]) -> Tuple[int, dict]:
    """Fail-closed authorization for /api/cron (v7, B02).

    Returns ``(status, payload)``; a 200 status means the request may proceed.
    The query string is intentionally never consulted — secrets in URLs end up
    in access logs.
    """
    if not secret:
        return 503, {"ok": False, "error": "misconfigured: CRON_SECRET is not set"}
    auth = ""
    try:
        auth = headers.get("authorization", "") or ""
    except AttributeError:
        auth = ""
    if auth != f"Bearer {secret}":
        return 401, {"ok": False, "error": "unauthorized"}
    return 200, {}


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0].rstrip("/") == "/api/health":
            self._send_json(200, {"ok": True, "service": "junior_middle_it"})
            return

        status, payload = authorize_cron_request(self.headers, Config.CRON_SECRET)
        if status != 200:
            self._send_json(status, payload)
            return

        try:
            result = asyncio.run(
                collect_and_post_once(
                    use_sqlite=False,
                    source_budget_seconds=int(os.getenv("SOURCE_BUDGET_SECONDS", "480")),
                )
            )
        except Exception:
            try:
                import sentry_sdk
                sentry_sdk.capture_exception()
            except Exception:
                pass
            # v7 (B04): traceback stays server-side (Sentry/logs), never in the body.
            logger.error("❌ /api/cron cycle failed", exc_info=True)
            self._send_json(500, {"ok": False, "error": "internal_error"})
            return
        self._send_json(200 if result.get("ok") else 500, result)

    def do_POST(self):
        self.do_GET()
