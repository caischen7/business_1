#!/usr/bin/env python3
"""Serve site/ on http://localhost:8765 for the smoke test. index.html is wrapped in the same
doctype skeleton claude.ai adds at publish time, so the page renders in standards mode."""
import functools
import http.server
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
SKELETON = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,'
            'initial-scale=1,viewport-fit=cover"><style>:root{color-scheme:light}body{margin:0;font:14px system-ui}'
            'img{max-width:100%}[hidden]{display:none!important}</style></head><body>{}</body></html>')


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            body = SKELETON.replace("{}", (SITE / "index.html").read_text(encoding="utf-8")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", 8765), functools.partial(Handler, directory=str(SITE))).serve_forever()
