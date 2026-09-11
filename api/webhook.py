import asyncio
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from localized_webhook_runtime import (
    MAX_UPDATE_BYTES,
    process_update_payload,
    webhook_secret_valid,
)
from secure_http_logging import configure_sensitive_http_logging

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
        self._send_json(405, {"ok": False, "error": "method_not_allowed"})

    def do_POST(self):
        if not webhook_secret_valid(
            self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        ):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        raw_length = self.headers.get("content-length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "invalid_content_length"})
            return
        if length < 0 or length > MAX_UPDATE_BYTES:
            self._send_json(413, {"ok": False, "error": "payload_too_large"})
            return

        body = self.rfile.read(length)
        if len(body) != length or len(body) > MAX_UPDATE_BYTES:
            self._send_json(400, {"ok": False, "error": "invalid_body"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("update must be an object")
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid_update"})
            return

        try:
            state = asyncio.run(process_update_payload(payload))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "invalid_update"})
            return
        except Exception as exc:
            logger.error("Telegram webhook processing failed: %s", type(exc).__name__)
            self._send_json(503, {"ok": False, "error": "temporarily_unavailable"})
            return

        if state == "busy":
            self._send_json(503, {"ok": False, "error": "update_busy"})
            return
        self._send_json(200, {"ok": True, "status": state})

    def log_message(self, fmt, *args):
        pass
