import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

from serverless_health import cron_authorized
from telegram_webhook_admin import disable_webhook, enable_webhook, webhook_status


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

    def _authorized(self) -> bool:
        return cron_authorized(self.headers.get("authorization", ""))

    def do_GET(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            info = webhook_status()
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send_json(200, {"ok": True, "webhook": info})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        query = parse_qs(urlsplit(self.path).query)
        action = str((query.get("action") or ["status"])[0]).strip().lower()
        try:
            if action == "enable":
                info = enable_webhook()
            elif action == "disable":
                info = disable_webhook()
            elif action == "status":
                info = webhook_status()
            else:
                self._send_json(400, {"ok": False, "error": "unsupported_action"})
                return
        except RuntimeError as exc:
            error = str(exc)
            status = 409 if error == "not_production" else 503
            self._send_json(status, {"ok": False, "error": error[:120]})
            return
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send_json(200, {"ok": True, "action": action, "webhook": info})

    def log_message(self, fmt, *args):
        pass
