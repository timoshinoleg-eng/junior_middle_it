import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

from public_site import load_recent_public_jobs, render_landing


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        category = (params.get("category") or [""])[0]
        level = (params.get("level") or [""])[0]

        jobs = load_recent_public_jobs(
            days=int(os.getenv("PUBLIC_JOBS_DAYS", "14")),
            limit=int(os.getenv("PUBLIC_JOBS_SOURCE_LIMIT", "120")),
        )
        body = render_landing(
            jobs,
            bot_username=os.getenv("BOT_USERNAME", ""),
            channel_id=os.getenv("CHANNEL_ID", ""),
            category=category,
            level=level,
            public_site_url=os.getenv("PUBLIC_SITE_URL", ""),
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "connect-src 'none'; font-src 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()
