"""Audit log commands.

Reads the local operations.jsonl audit log under .phlo/audit, filtered by
operation and by an ISO-8601 timestamp or relative duration such as 15m or
7d. Plain mode prints one JSON record per line; --json emits a single
envelope with all matching records.

Wired into the phlo CLI command tree by src/phlo/cli/main.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click

from phlo.cli.output import json_envelope


@click.group(name="audit")
def audit_group() -> None:
    """Inspect local Phlo audit records."""


def _audit_path() -> Path:
    return Path(".phlo") / "audit" / "operations.jsonl"


def _read_records(limit: int | None = None, since: str | None = None) -> list[dict[str, Any]]:
    path = _audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    # Malformed lines are kept as records instead of being dropped; an audit
    # trail must not lose entries it cannot parse.
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"malformed": line})
    if since:
        threshold = _parse_since(since)
        records = [record for record in records if _record_timestamp(record) >= threshold]
    if limit is not None:
        records = records[-limit:]
    return records


def _record_timestamp(record: dict[str, Any]) -> datetime:
    value = str(record.get("timestamp") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Sorts before every real timestamp, so `--since` filters out
        # records lacking a valid one.
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_since(value: str) -> datetime:
    now = datetime.now(UTC)
    value = value.strip()
    if value.endswith("m") and value[:-1].isdigit():
        return now - timedelta(minutes=int(value[:-1]))
    if value.endswith("h") and value[:-1].isdigit():
        return now - timedelta(hours=int(value[:-1]))
    if value.endswith("d") and value[:-1].isdigit():
        return now - timedelta(days=int(value[:-1]))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.ClickException(
            "--since must be ISO-8601 or a relative duration like 15m, 1h, or 7d"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@audit_group.command("tail")
@click.option("--limit", default=20, show_default=True)
@click.option("--since", help="ISO-8601 timestamp or relative duration like 15m, 1h, or 7d.")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def tail_cmd(limit: int, since: str | None, output_json: bool) -> None:
    """Tail recent audit records."""
    if limit <= 0:
        raise click.BadParameter("must be greater than 0", param_hint="--limit")
    records = _read_records(limit=limit, since=since)
    if output_json:
        click.echo(json_envelope(data={"items": records, "since": since}))
        return
    for record in records:
        click.echo(json.dumps(record, sort_keys=True))


@audit_group.command("query")
@click.option("--operation")
@click.option("--since", help="ISO-8601 timestamp or relative duration like 15m, 1h, or 7d.")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def query_cmd(operation: str | None, since: str | None, output_json: bool) -> None:
    """Query audit records."""
    records = _read_records(since=since)
    if operation:
        records = [record for record in records if record.get("operation") == operation]
    if output_json:
        click.echo(json_envelope(data={"items": records}))
        return
    for record in records:
        click.echo(json.dumps(record, sort_keys=True))


__all__ = ["audit_group"]
