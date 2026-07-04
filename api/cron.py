import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from channel_bot import Config, collect_and_post_once


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

        secret = Config.CRON_SECRET
        if secret:
            auth = self.headers.get("authorization", "")
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            if auth != f"Bearer {secret}" and f"secret={secret}" not in query:
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return

        result = asyncio.run(
            collect_and_post_once(
                use_sqlite=False,
                source_budget_seconds=int(os.getenv("SOURCE_BUDGET_SECONDS", "480")),
            )
        )
        self._send_json(200 if result.get("ok") else 500, result)

    def do_POST(self):
        self.do_GET()
