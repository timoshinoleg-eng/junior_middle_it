from http.server import BaseHTTPRequestHandler


ROBOTS = b"User-agent: *\nAllow: /\nDisallow: /api/\n\n"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(ROBOTS)))
        self.send_header("Cache-Control", "public, s-maxage=3600, stale-while-revalidate=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(ROBOTS)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(ROBOTS)))
        self.send_header("Cache-Control", "public, s-maxage=3600, stale-while-revalidate=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
