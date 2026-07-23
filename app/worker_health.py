"""Tiny HTTP health endpoint for Railway worker services."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_health_server(role: str) -> None:
    port = int(os.getenv("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"ok": True, "role": role}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
