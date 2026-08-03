"""Render.com entrypoint for the interactive job bot.

Render free web services must bind $PORT (HTTP) or they spin down / fail
health checks. channel_bot.py is a pure long-polling worker with no HTTP
surface, so this wrapper:

1. Starts a minimal health HTTP server on $PORT in a daemon thread.
2. Runs channel_bot.main() (interactive bot + collection loop) on the
   main thread.

Keep-alive: point UptimeRobot (free, 5-min interval) at /health so the
free instance never idles past the 15-minute spin-down threshold.

Local run:  PORT=8080 python render_main.py
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Render ephemeral FS: don't keep a bot.log file around (writes are lost
# on spin-down anyway and waste I/O).
os.environ.setdefault("DISABLE_FILE_LOG", "true")


class HealthHandler(BaseHTTPRequestHandler):
    started_at = None

    def do_GET(self):
        if self.path in ("/health", "/healthz", "/"):
            body = b'{"ok": true, "service": "junior_middle_it_bot"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence per-request logs
        pass


def serve_health(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.daemon_threads = True
    server.serve_forever()


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    t = threading.Thread(target=serve_health, args=(port,), daemon=True)
    t.start()
    print(f"[render_main] health server on :{port}/health", flush=True)

    import channel_bot
    import asyncio

    try:
        asyncio.run(channel_bot.main())
    except KeyboardInterrupt:
        print("[render_main] stopped by user", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
