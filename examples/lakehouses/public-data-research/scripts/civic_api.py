"""Replay API serving deterministic civic place-registry payloads from fixtures."""

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
DEFAULT_PORT = 8094


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class ReplayHandler(BaseHTTPRequestHandler):
    """Serve paginated place-registry payloads from generated fixtures."""

    data_dir: Path = DEFAULT_DATA_DIR

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/v1/places":
                self._send(
                    self._places(params["registry_date"][0], int(params.get("cursor", ["0"])[0]))
                )
            else:
                self.send_error(404, "unknown endpoint")
        except KeyError as exc:
            self.send_error(400, f"missing parameter: {exc}")
        except FileNotFoundError:
            self.send_error(404, "no fixture for requested registry date")

    def _places(self, registry_date: str, cursor: int) -> dict[str, object]:
        payload = _load_json(self.data_dir / "api" / f"place-registry-{registry_date}.json")
        pages = cast("list[list[dict[str, object]]]", payload["pages"])
        if cursor >= len(pages):
            self.send_error(404, "cursor past end of pagination")
            return {}
        next_cursor: int | None = cursor + 1 if cursor + 1 < len(pages) else None
        return {"data": pages[cursor], "next_cursor": next_cursor}

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
    print(f"Serving civic replay fixtures from {args.data_dir} on port {args.port}")
    try:
        threading.Event().wait()
    finally:
        server.shutdown()
