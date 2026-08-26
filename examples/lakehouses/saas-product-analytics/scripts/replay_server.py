"""Deterministic nested SaaS REST replay with pagination and a one-time 429."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


class ReplayHandler(BaseHTTPRequestHandler):
    pages: list[list[dict[str, object]]]
    rate_limited = False

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        if request.path != "/v1/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        cursor = parse_qs(request.query).get("cursor", ["0"])[0]
        if cursor == "1" and not type(self).rate_limited:
            type(self).rate_limited = True
            self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        page = int(cursor)
        payload = {
            "data": self.pages[page] if page < len(self.pages) else [],
            "next_cursor": str(page + 1) if page + 1 < len(self.pages) else None,
        }
        encoded = json.dumps(payload).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(data_dir: Path, port: int, events_file: str = "events.json") -> None:
    ReplayHandler.pages = json.loads((data_dir / events_file).read_text(encoding="utf-8"))
    ReplayHandler.rate_limited = False
    server = ThreadingHTTPServer(("0.0.0.0", port), ReplayHandler)
    print(f"replay server listening on http://127.0.0.1:{port}/v1/events")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "generated-data")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--events-file", default="events.json")
    args = parser.parse_args()
    serve(args.data_dir, args.port, args.events_file)
