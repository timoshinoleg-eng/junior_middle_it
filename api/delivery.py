import asyncio
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Install the localized interactive runtime before scheduled_delivery_runtime
# captures build_runtime. Public channel digests remain channel-wide; personal
# daily roundups use each user's selected language.
import localized_webhook_runtime  # noqa: F401
from scheduled_delivery_runtime import run_scheduled_delivery
from secure_http_logging import configure_sensitive_http_logging
from serverless_health import cron_authorized

configure_sensitive_http_logging()
logger = logging.getLogger(__name__)


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
        self.do_POST()

    def do_POST(self):
        if not cron_authorized(self.headers.get("authorization", "")):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        query = parse_qs(urlsplit(self.path).query)
        kind = str((query.get("kind") or [""])[0]).strip().lower()
        if kind not in {"daily", "weekly"}:
            self._send_json(400, {"ok": False, "error": "unsupported_kind"})
            return
        try:
            result = asyncio.run(run_scheduled_delivery(kind))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "unsupported_kind"})
            return
        except Exception as exc:
            logger.error("Scheduled delivery failed: %s", type(exc).__name__)
            self._send_json(503, {"ok": False, "error": "temporarily_unavailable"})
            return
        self._send_json(200, dict(result or {"ok": True, "kind": kind}))

    def log_message(self, fmt, *args):
        pass
