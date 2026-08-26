"""REST replay server for the firmware-v2 telemetry batch.

Serves ``generated-data/evolved/readings_v2.csv`` as JSON so the live path
exercises an HTTP extraction while staying fully deterministic. Without the
server the same rows are read offline by ``workflows.ingest.evolution``.
"""

from __future__ import annotations

import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"
DEFAULT_PORT = 8093


def _load_rows(data_dir: Path) -> list[dict[str, str]]:
    with (data_dir / "evolved" / "readings_v2.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_handler(rows: list[dict[str, str]]) -> type[BaseHTTPRequestHandler]:
    """Return a request handler exposing the evolved batch."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.rstrip("/") == "/v1/readings/v2":
                body = json.dumps({"data": rows}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404, "unknown replay endpoint")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    return Handler


def serve(data_dir: Path = DEFAULT_DATA_DIR, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Start the replay server; returns the running server for callers."""
    handler = build_handler(_load_rows(data_dir))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seconds", type=float, default=None)
    args = parser.parse_args()
    server = serve(args.data_dir, args.port)
    print(f"Serving evolved telemetry replay from {args.data_dir} on port {args.port}")
    try:
        if args.seconds is None:
            import signal

            signal.pause()
        else:
            import time

            time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
