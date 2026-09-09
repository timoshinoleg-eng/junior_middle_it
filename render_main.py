"""Render.com entrypoint for the interactive job bot.

The health HTTP server keeps Render healthy while the long-polling Telegram bot
runs in a worker thread. Production bot imports go through ``channel_bot_v2`` so
durable growth state, fail-closed admin auth and activation UX are installed
without rewriting the mature ingestion core.
"""
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Render ephemeral FS: don't keep a bot.log file around.
os.environ.setdefault("DISABLE_FILE_LOG", "true")


def config_summary() -> dict:
    """Presence flags for key env vars — safe to expose (no secret values)."""
    def present(*names):
        return any(os.getenv(n) for n in names)

    return {
        "TELEGRAM_BOT_TOKEN": present("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_BOT_ID": (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").split(":")[0] if present("TELEGRAM_BOT_TOKEN") else "",
        "CHANNEL_ID": present("CHANNEL_ID"),
        "CHANNEL_ID_VAL": os.getenv("CHANNEL_ID", "")[:30] if present("CHANNEL_ID") else "",
        "ADMIN_USER_ID": present("ADMIN_USER_ID"),
        "GROWTH_DATABASE": present("GROWTH_DATABASE_URL", "DATABASE_URL"),
        "REQUIRE_DURABLE_GROWTH": os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true",
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


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/healthz", "/"):
            with STATE_LOCK:
                running = STATE["bot_running"] and not STATE["error"]
                payload = {
                    "ok": running,
                    "service": "junior_middle_it_bot",
                    "uptime_s": int(time.time() - STATE["started_at"]),
                    "bot": "running" if running else "crashed" if STATE["error"] else "starting",
                    "error": STATE["error"],
                    "config": config_summary(),
                    "cycle": None,
                }
            try:
                import channel_bot_v2 as _cb
                if getattr(_cb, "CYCLE_TELEMETRY", None):
                    payload["cycle"] = dict(_cb.CYCLE_TELEMETRY)
            except Exception:
                pass
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def run_bot() -> None:
    with STATE_LOCK:
        STATE["bot_thread_started"] = True
    try:
        import asyncio
        import channel_bot_v2

        with STATE_LOCK:
            STATE["bot_running"] = True
        print("[render_main] channel_bot_v2 imported, entering main()", flush=True)
        asyncio.run(channel_bot_v2.main())
        with STATE_LOCK:
            STATE["bot_running"] = False
            STATE["error"] = "channel_bot_v2.main() returned unexpectedly"
    except BaseException as e:
        tb = traceback.format_exc()
        print(f"[render_main] BOT CRASHED: {tb}", flush=True)
        with STATE_LOCK:
            STATE["bot_running"] = False
            STATE["error"] = f"{type(e).__name__}: {e} | ...{tb[-400:]}"


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    t = threading.Thread(target=run_bot, name="bot-worker", daemon=False)
    t.start()
    print(f"[render_main] health server on :{port}/health", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
