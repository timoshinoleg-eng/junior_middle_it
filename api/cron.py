import asyncio
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Configure sensitive third-party loggers before importing the Telegram runtime.
# httpx INFO logs include the full Bot API URL, whose path embeds the bot token.
from secure_http_logging import configure_sensitive_http_logging

configure_sensitive_http_logging()

from serverless_health import (
    cron_authorized,
    durable_growth_configured,
    missing_posting_env,
)
from serverless_payload_runtime import collect_and_post_once
from sentry_setup import init_sentry

logger = logging.getLogger(__name__)
init_sentry()


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
        missing = missing_posting_env()
        if "CRON_SECRET" in missing:
            self._send_json(503, {"ok": False, "error": "cron_not_configured"})
            return

        if not cron_authorized(self.headers.get("authorization", "")):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        collector_missing = [
            name for name in missing if name in {"TELEGRAM_BOT_TOKEN", "CHANNEL_ID"}
        ]
        if collector_missing:
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": "collector_not_configured",
                    "missing_required": collector_missing,
                },
            )
            return

        try:
            result = asyncio.run(
                collect_and_post_once(
                    use_sqlite=False,
                    source_budget_seconds=int(os.getenv("SOURCE_BUDGET_SECONDS", "480")),
                )
            )
            result = dict(result or {})
            result["durable_growth"] = durable_growth_configured()
        except Exception as exc:
            logger.exception("Vercel cron collection failed")
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
            self._send_json(500, {"ok": False, "error": "internal_error"})
            return

        self._send_json(200 if result.get("ok") else 500, result)

    def do_POST(self):
        self.do_GET()
