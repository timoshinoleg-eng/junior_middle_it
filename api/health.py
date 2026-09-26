import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from serverless_health import readiness


def _counters_requested(path: str) -> bool:
    """Publication counters cost one remote read, so serve them on demand."""
    if (os.getenv("HEALTH_PUBLICATION_COUNTERS", "true") or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    return "counters=0" not in (path or "")


class handler(BaseHTTPRequestHandler):
    def _payload(self) -> dict:
        return readiness(include_publication_counters=_counters_requested(self.path))

    def do_GET(self):
        payload = self._payload()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if payload["ok"] else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        payload = self._payload()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if payload["ok"] else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
