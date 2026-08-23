"""Core telemetry recording helpers.

TelemetryRecorder appends TelemetryEvent payloads as JSONL, rotating
the file by size (timestamped rename) rather than truncating history.
Events are redacted before serialization; a failed record is logged and
re-raised so callers cannot silently lose events. iter_telemetry_events
skips malformed lines instead of failing the whole scan.
Imported by phlo core (capabilities package, hooks telemetry) and the phlo-api observatory API.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phlo.hooks import TelemetryEvent
from phlo.logging import get_logger, redact_sensitive_fields

logger = get_logger(__name__)


class TelemetryRecorder:
    """Write telemetry events to a JSONL file."""

    def __init__(self, path: Path | None = None, max_bytes: int = 20_000_000) -> None:
        self.path = path or _default_path()
        self.max_bytes = max_bytes

    def record(self, event: TelemetryEvent) -> None:
        """Append a telemetry event to the JSONL file, rotating if needed."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            payload = _serialize_event(event)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, default=str) + "\n")
        except Exception:
            logger.warning("telemetry_record_failed", path=str(self.path), exc_info=True)
            raise

    def _rotate_if_needed(self) -> None:
        """Rotate the telemetry file when it exceeds max_bytes."""
        if not self.path.exists():
            return
        if self.path.stat().st_size < self.max_bytes:
            return
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        rotated = self.path.with_name(f"{self.path.stem}.{timestamp}{self.path.suffix}")
        self.path.rename(rotated)
        logger.debug(
            "telemetry_file_rotated",
            source_path=str(self.path),
            rotated_path=str(rotated),
            max_bytes=self.max_bytes,
        )


def _default_path() -> Path:
    """Return the default telemetry output path."""
    env_path = os.environ.get("PHLO_TELEMETRY_PATH")
    if env_path:
        return Path(env_path)
    return Path.cwd() / ".phlo" / "telemetry" / "events.jsonl"


def get_telemetry_path(path: Path | None = None) -> Path:
    """Resolve the telemetry JSONL path."""
    return path or _default_path()


def iter_telemetry_events(path: Path | None = None) -> Iterator[dict[str, Any]]:
    """Yield telemetry events from the JSONL file."""
    event_path = get_telemetry_path(path)
    if not event_path.exists():
        return iter(())

    def _iter() -> Iterator[dict[str, Any]]:
        with event_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        "telemetry_event_decode_failed",
                        path=str(event_path),
                        line=line,
                        exc_info=True,
                    )
                    continue
                if isinstance(payload, dict):
                    yield payload

    return _iter()


def _serialize_event(event: TelemetryEvent) -> dict[str, Any]:
    """Serialize a TelemetryEvent into JSON-friendly primitives."""
    payload = asdict(event)
    payload["timestamp"] = event.timestamp.isoformat()
    redact_sensitive_fields(payload)
    return payload
