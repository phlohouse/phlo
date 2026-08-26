"""Replay API serving deterministic carrier event scans from fixtures.

Endpoints:
    GET /v1/events?carrier=ATLAS&date=YYYY-MM-DD
        -> {"events": [...]} served from generated-data/carriers/<CARRIER>/<date>.json

The server is intentionally tiny: it replays bytes the fixture generator wrote,
so live materializations are as deterministic as pytest runs.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"
DEFAULT_PORT = 8090


class ReplayHandler(BaseHTTPRequestHandler):
    """Serve carrier-shaped event payloads from generated fixtures."""

    data_dir: Path = DEFAULT_DATA_DIR

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/v1/events":
                self._send(self._events(params["carrier"][0], params["date"][0]))
            else:
                self.send_error(404, "unknown endpoint")
        except KeyError as exc:
            self.send_error(400, f"missing parameter: {exc}")
        except FileNotFoundError:
            self.send_error(404, "no fixture for requested carrier/date")

    def _events(self, carrier: str, event_date: str) -> dict[str, object]:
        payload = json.loads(
            (self.data_dir / "carriers" / carrier / f"{event_date}.json").read_text()
        )
        return {"events": payload["events"]}

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
    print(f"Serving carrier replay fixtures from {args.data_dir} on port {args.port}")
    try:
        threading.Event().wait()
    finally:
        server.shutdown()
