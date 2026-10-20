"""Replay API serving deterministic support-ticket payloads from fixtures."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"
DEFAULT_PORT = 8093


class ReplayHandler(BaseHTTPRequestHandler):
    """Serve the ticket payload exactly as generated; no clocks involved."""

    data_dir: Path = DEFAULT_DATA_DIR

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/v1/tickets":
                del params["scope"]
                self._send({"data": self._tickets()})
            else:
                self.send_error(404, "unknown endpoint")
        except KeyError as exc:
            self.send_error(400, f"missing parameter: {exc}")
        except FileNotFoundError:
            self.send_error(404, "no fixture for requested resource")

    def _tickets(self) -> list[dict[str, object]]:
        tickets_path = self.data_dir / "support" / "tickets.json"
        payload = json.loads(tickets_path.read_text(encoding="utf-8"))
        return cast("list[dict[str, object]]", payload["tickets"])

    def _send(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        print(f"{self.address_string()} - {format % args}")


def serve(data_dir: Path = DEFAULT_DATA_DIR, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Start the replay server; returns the running server for callers."""
    handler = type("BoundReplayHandler", (ReplayHandler,), {"data_dir": data_dir})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = serve(args.data_dir, args.port)
    print(f"Serving support replay fixtures from {args.data_dir} on port {args.port}")
    try:
        threading.Event().wait()
    finally:
        server.shutdown()
