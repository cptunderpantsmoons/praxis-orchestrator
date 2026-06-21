"""Lightweight HTTP wrapper for LDR's programmatic API.

Runs inside the LDR container (or alongside it on the same network).
Exposes quick_summary() and generate_report() via a simple REST endpoint
with API key auth — no Flask session/CSRF complexity.

Usage:
    docker exec -d local-deep-research-xvsy-local-deep-research-1 \\
        /install/.venv/bin/python /scripts/ldr_api_wrapper.py

    POST /research
        {"query": "...", "api_key": "...", "mode": "quick|full", "searches_per_section": 1}
"""
import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# LDR's programmatic API
from local_deep_research.api.research_functions import quick_summary, generate_report
from local_deep_research.api.settings_utils import create_settings_snapshot

API_KEY = os.environ.get("LDR_WRAPPER_API_KEY", "praxis_ldr_2026")
PORT = int(os.environ.get("LDR_WRAPPER_PORT", "5001"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/research":
            return self._json(404, {"error": "Not found"})

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._json(400, {"error": "Invalid JSON"})

        if payload.get("api_key") != API_KEY:
            return self._json(401, {"error": "Invalid API key"})

        query = payload.get("query", "").strip()
        if not query:
            return self._json(400, {"error": "query is required"})

        mode = payload.get("mode", "quick")
        searches_per_section = min(int(payload.get("searches_per_section", 1)), 2)

        try:
            # Create settings snapshot for programmatic mode
            settings = create_settings_snapshot({"programmatic_mode": True})

            if mode == "full":
                result = generate_report(
                    query=query,
                    searches_per_section=searches_per_section,
                    settings_snapshot=settings,
                )
            else:
                result = quick_summary(
                    query=query,
                    settings_snapshot=settings,
                )

            return self._json(200, {
                "success": True,
                "query": query,
                "result": result,
            })

        except Exception as exc:
            return self._json(500, {
                "success": False,
                "error": str(exc),
                "type": type(exc).__name__,
            })

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        return self._json(404, {"error": "Not found"})

    def log_message(self, *args):
        pass  # suppress default logging


def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"LDR API wrapper listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
