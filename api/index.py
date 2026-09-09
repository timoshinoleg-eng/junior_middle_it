import asyncio
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

from public_site import load_recent_public_jobs, render_landing
from serverless_health import (
    cron_authorized,
    durable_growth_configured,
    missing_posting_env,
    readiness,
)

logger = logging.getLogger(__name__)

ROBOTS = b"User-agent: *\nAllow: /\nDisallow: /api/\n\n"
KNOWN_ROUTES = {"site", "robots", "health", "cron"}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def resolve_route(path: str) -> str:
    """Resolve the explicit rewrite target, with path fallbacks for local tests."""
    parsed = urlsplit(path or "/")
    params = parse_qs(parsed.query)
    explicit = (params.get("route") or [""])[0].strip().lower()
    if explicit in KNOWN_ROUTES:
        return explicit

    normalized = parsed.path.rstrip("/") or "/"
    return {
        "/": "site",
        "/robots.txt": "robots",
        "/api/health": "health",
        "/api/cron": "cron",
        "/api/index": "",
    }.get(normalized, "")


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict, *, include_body: bool = True) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        cache_control: str,
        *,
        include_body: bool = True,
        extra_headers: dict | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _site(self, *, include_body: bool) -> None:
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        category = (params.get("category") or [""])[0]
        level = (params.get("level") or [""])[0]

        jobs = load_recent_public_jobs(
            days=_bounded_env_int("PUBLIC_JOBS_DAYS", 14, 1, 45),
            limit=_bounded_env_int("PUBLIC_JOBS_SOURCE_LIMIT", 120, 1, 300),
        )
        body = render_landing(
            jobs,
            bot_username=os.getenv("BOT_USERNAME", ""),
            channel_id=os.getenv("CHANNEL_ID", ""),
            category=category,
            level=level,
            public_site_url=os.getenv("PUBLIC_SITE_URL", ""),
        ).encode("utf-8")
        self._send_bytes(
            200,
            body,
            "text/html; charset=utf-8",
            "public, s-maxage=300, stale-while-revalidate=3600",
            include_body=include_body,
            extra_headers={
                "Referrer-Policy": "strict-origin-when-cross-origin",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
                    "connect-src 'none'; font-src 'none'"
                ),
            },
        )

    def _robots(self, *, include_body: bool) -> None:
        self._send_bytes(
            200,
            ROBOTS,
            "text/plain; charset=utf-8",
            "public, s-maxage=3600, stale-while-revalidate=86400",
            include_body=include_body,
        )

    def _health(self, *, include_body: bool) -> None:
        payload = readiness()
        self._send_json(200 if payload["ok"] else 503, payload, include_body=include_body)

    def _cron(self, *, include_body: bool) -> None:
        missing = missing_posting_env()
        if "CRON_SECRET" in missing:
            self._send_json(503, {"ok": False, "error": "cron_not_configured"}, include_body=include_body)
            return
        if not cron_authorized(self.headers.get("authorization", "")):
            self._send_json(401, {"ok": False, "error": "unauthorized"}, include_body=include_body)
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
                include_body=include_body,
            )
            return

        if not include_body:
            self._send_json(405, {"ok": False, "error": "method_not_allowed"}, include_body=False)
            return

        try:
            from serverless_payload_runtime import collect_and_post_once
            from sentry_setup import init_sentry

            init_sentry()
            result = asyncio.run(
                collect_and_post_once(
                    use_sqlite=False,
                    source_budget_seconds=_bounded_env_int(
                        "SOURCE_BUDGET_SECONDS", 480, 30, 780
                    ),
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

    def _dispatch(self, *, include_body: bool = True) -> None:
        route = resolve_route(self.path)
        if route == "site":
            self._site(include_body=include_body)
        elif route == "robots":
            self._robots(include_body=include_body)
        elif route == "health":
            self._health(include_body=include_body)
        elif route == "cron":
            self._cron(include_body=include_body)
        else:
            self._send_json(404, {"ok": False, "error": "not_found"}, include_body=include_body)

    def do_GET(self):
        self._dispatch(include_body=True)

    def do_POST(self):
        route = resolve_route(self.path)
        if route != "cron":
            self._send_json(405, {"ok": False, "error": "method_not_allowed"})
            return
        self._cron(include_body=True)

    def do_HEAD(self):
        route = resolve_route(self.path)
        if route == "cron":
            self._send_json(405, {"ok": False, "error": "method_not_allowed"}, include_body=False)
            return
        self._dispatch(include_body=False)
