"""Render.com entrypoint for the interactive job bot.

The HTTP endpoint is a truthful liveness/readiness probe for the persistent
Telegram worker. Production imports go through ``public_acquisition_runtime`` so
P1-P7 are installed without changing the Vercel ingestion runtime.
"""
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List

os.environ.setdefault("DISABLE_FILE_LOG", "true")

REQUIRED_ENV = ("TELEGRAM_BOT_TOKEN", "CHANNEL_ID")


def missing_required_env() -> List[str]:
    """Return missing production-critical settings without exposing values."""
    return [name for name in REQUIRED_ENV if not (os.getenv(name) or "").strip()]


def config_summary() -> dict:
    """Presence flags for operationally relevant env vars; never expose values."""
    def present(*names):
        return any((os.getenv(n) or "").strip() for n in names)

    return {
        "TELEGRAM_BOT_TOKEN": present("TELEGRAM_BOT_TOKEN"),
        "CHANNEL_ID": present("CHANNEL_ID"),
        "ADMIN_USER_ID": present("ADMIN_USER_ID"),
        "GROWTH_DATABASE": present("GROWTH_DATABASE_URL", "DATABASE_URL"),
        "REQUIRE_DURABLE_GROWTH": os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true",
        "RESUME_MATCH": True,
        "RESUME_PDF_DOCX": True,
        "SAVED_SEARCHES": True,
        "CONTENT_MAGNETS": True,
        "PRODUCT_UTILITY_METRICS": True,
        "REFERRAL_2": True,
        "PUBLIC_ACQUISITION": True,
        "APIFY_API_TOKEN": present("APIFY_API_TOKEN"),
        "TELEGRAM_API_ID": present("TELEGRAM_API_ID"),
        "TELEGRAM_API_HASH": present("TELEGRAM_API_HASH"),
        "SUPERJOB_API_KEY": present("SUPERJOB_API_KEY"),
        "ADZUNA_APP_ID": present("ADZUNA_APP_ID"),
        "GREENHOUSE_BOARDS": present("GREENHOUSE_BOARDS"),
        "DEDUP_MODE": os.getenv("DEDUP_MODE", "sqlite"),
        "CHECK_INTERVAL": os.getenv("CHECK_INTERVAL", "1800"),
    }


STATE = {
    "started_at": time.time(),
    "bot_thread_started": False,
    "bot_running": False,
    "error": None,
}
STATE_LOCK = threading.Lock()


def health_snapshot() -> tuple[int, dict]:
    """Build a sanitized readiness response and matching HTTP status."""
    with STATE_LOCK:
        running = bool(STATE["bot_running"] and not STATE["error"])
        failed = bool(STATE["error"])
        payload = {
            "ok": running,
            "service": "junior_middle_it_bot",
            "uptime_s": int(time.time() - STATE["started_at"]),
            "bot": "running" if running else "crashed" if failed else "starting",
            "error": STATE["error"],
            "config": config_summary(),
            "cycle": None,
        }
    try:
        import public_acquisition_runtime as runtime
        if getattr(runtime, "CYCLE_TELEMETRY", None):
            payload["cycle"] = dict(runtime.CYCLE_TELEMETRY)
    except Exception:
        pass
    return (200 if running else 503), payload


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/health", "/healthz", "/"):
            status, payload = health_snapshot()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def _set_runtime_failure(label: str) -> None:
    with STATE_LOCK:
        STATE["bot_running"] = False
        STATE["error"] = label


def run_bot(exit_fn: Callable[[int], None] = os._exit) -> None:
    """Run the worker; any unexpected stop is fatal for the whole service."""
    with STATE_LOCK:
        STATE["bot_thread_started"] = True
    try:
        import asyncio
        import public_acquisition_runtime

        with STATE_LOCK:
            STATE["bot_running"] = True
            STATE["error"] = None
        print("[render_main] public_acquisition_runtime imported, entering main()", flush=True)
        asyncio.run(public_acquisition_runtime.main())
        _set_runtime_failure("bot_runtime_returned")
        print("[render_main] BOT STOPPED: main() returned unexpectedly", flush=True)
        exit_fn(1)
    except BaseException as exc:
        tb = traceback.format_exc()
        print(f"[render_main] BOT CRASHED: {tb}", flush=True)
        _set_runtime_failure(f"bot_runtime_failed:{type(exc).__name__}")
        exit_fn(1)


def main() -> None:
    missing = missing_required_env()
    if missing:
        print(
            "[render_main] missing required environment: " + ", ".join(missing),
            flush=True,
        )
        raise SystemExit(2)

    port = int(os.environ.get("PORT", "8080"))
    worker = threading.Thread(target=run_bot, name="bot-worker", daemon=True)
    worker.start()
    print(f"[render_main] health server on :{port}/health", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
