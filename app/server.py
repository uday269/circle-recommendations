"""Minimal JSON HTTP API on the Python standard library (no install step for reviewers).

    python -m app.server            # rebuilds circles.db from data/ and serves on :8000
    GET /health
    GET /users/{user_id}/recommendations?limit=5&as_of=2026-09-21T00:00:00Z&debug=true
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db
from .service import BadRequest, NotFound, get_recommendations

# Landing page so anyone opening the base URL sees where to go (the API itself is JSON).
INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Circle Recommendations API</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.5}
td{padding:6px 12px 6px 0;vertical-align:top}code{background:#f2f2f2;padding:1px 4px}</style></head><body>
<h1>Circle Recommendations API</h1>
<p>The server is running. Recommendations are at <code>/users/{user_id}/recommendations</code>.
Click a planted scenario (add <code>?debug=true</code> to see scores and excluded Circles):</p>
<table>
<tr><td>Obvious matches</td><td><a href="/users/7/recommendations">user 7</a> &middot; <a href="/users/7/recommendations?debug=true">debug</a></td></tr>
<tr><td>Duplicate Circles</td><td><a href="/users/15/recommendations">user 15</a> &middot; <a href="/users/15/recommendations?debug=true">debug</a></td></tr>
<tr><td>Full / inactive</td><td><a href="/users/23/recommendations">user 23</a> &middot; <a href="/users/23/recommendations?debug=true">debug</a></td></tr>
<tr><td>Cold start</td><td><a href="/users/42/recommendations">user 42</a> &middot; <a href="/users/42/recommendations?debug=true">debug</a></td></tr>
</table>
<p>Any user id from 1 to 100 works. Options: <code>limit</code> (1&ndash;20), <code>as_of</code> (ISO date), <code>debug</code>.</p>
</body></html>"""

ROUTE_RECS = re.compile(r"^/users/(\d+)/recommendations/?$")
TRUTHY = {"1", "true", "yes"}


def make_handler(conn, quiet: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CircleRecs/1.0"

        def log_message(self, fmt, *args):  # noqa: D401 - stdlib hook
            if not quiet:
                super().log_message(fmt, *args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    return self._send_html(INDEX_HTML)
                if url.path == "/health":
                    return self._send(200, {"status": "ok"})
                m = ROUTE_RECS.match(url.path)
                if m:
                    try:
                        limit = int(q.get("limit", 5))
                    except ValueError:
                        raise BadRequest("limit must be an integer")
                    return self._send(200, get_recommendations(
                        conn, int(m.group(1)), limit=limit, as_of=q.get("as_of"),
                        debug=q.get("debug", "").lower() in TRUTHY))
                return self._send(404, {"error": "not_found", "message": f"no route for {url.path}"})
            except NotFound as e:
                return self._send(404, {"error": "not_found", "message": str(e)})
            except BadRequest as e:
                return self._send(400, {"error": "bad_request", "message": str(e)})
            except Exception as e:  # pragma: no cover - last-resort guard
                self.log_error("unhandled error: %r", e)
                return self._send(500, {"error": "internal_error"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Circle recommendations API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    conn = db.build_database()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(conn))
    print(f"Serving on http://{args.host}:{args.port}  (try /users/7/recommendations)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
