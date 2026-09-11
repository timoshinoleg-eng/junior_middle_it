import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

from public_growth_client import load_recent_public_jobs_http
from public_site import load_recent_public_jobs, render_landing


_MAX_REASONABLE_FUTURE_SKEW = timedelta(hours=6)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _public_site_url() -> str:
    """Return the explicit public URL, or Vercel's canonical production domain."""
    explicit = (os.getenv("PUBLIC_SITE_URL") or "").strip()
    if explicit:
        return explicit
    production_domain = (os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or "").strip()
    if not production_domain:
        return ""
    if production_domain.startswith(("http://", "https://")):
        return production_domain
    return f"https://{production_domain}"


def _parse_source_published_at(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        try:
            raw = float(text)
            if raw > 10_000_000_000:
                raw /= 1000.0
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_fresh_public_job(job: dict, *, max_age_days: int = 7, now=None) -> bool:
    """Expose only vacancies whose original source date is verifiably fresh."""
    published = _parse_source_published_at(job.get("source_published_at"))
    if published is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    if published - current > _MAX_REASONABLE_FUTURE_SKEW:
        return False
    return current - published <= timedelta(days=max(1, min(int(max_age_days), 30)))


class handler(BaseHTTPRequestHandler):
    def _render(self) -> bytes:
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        category = (params.get("category") or [""])[0]
        level = (params.get("level") or [""])[0]
        days = _bounded_env_int("PUBLIC_JOBS_DAYS", 14, 1, 45)
        source_limit = _bounded_env_int("PUBLIC_JOBS_SOURCE_LIMIT", 120, 1, 300)
        fresh_days = _bounded_env_int("PUBLIC_MAX_JOB_AGE_DAYS", 7, 1, 30)

        jobs = load_recent_public_jobs(days=days, limit=source_limit)
        if not jobs:
            jobs = load_recent_public_jobs_http(
                os.getenv("GROWTH_PUBLIC_URL", ""),
                days=days,
                limit=source_limit,
            )
        jobs = [job for job in jobs if _is_fresh_public_job(job, max_age_days=fresh_days)]
        return render_landing(
            jobs,
            bot_username=os.getenv("BOT_USERNAME", ""),
            channel_id=os.getenv("CHANNEL_ID", ""),
            category=category,
            level=level,
            public_site_url=_public_site_url(),
        ).encode("utf-8")

    def _send_headers(self, content_length: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(content_length))
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

    def do_GET(self):
        body = self._render()
        self._send_headers(len(body))
        self.wfile.write(body)

    def do_HEAD(self):
        body = self._render()
        self._send_headers(len(body))
