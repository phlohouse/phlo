"""Core metrics CLI tests.

Covers metrics model creation, JSON export, byte formatting, period parsing,
and the summary command in text and JSON output modes.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from phlo.cli.commands.metrics import _export_json, _format_bytes, _parse_period, metrics_group
from phlo.metrics import SummaryMetrics


def test_export_json(tmp_path: Path) -> None:
    output = tmp_path / "metrics.json"
    _export_json(SummaryMetrics(total_runs_24h=1), output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_runs_24h"] == 1
    assert "exported_at" in payload


def test_format_bytes() -> None:
    assert _format_bytes(1024) == "1.00 KB"
    assert _format_bytes(1024 * 1024) == "1.00 MB"


def test_parse_period() -> None:
    assert _parse_period("24h") == 24
    assert _parse_period("7d") == 168
    assert _parse_period("2w") == 336
    assert _parse_period("bogus") == 24


def test_metrics_summary_command(monkeypatch) -> None:
    class FakeCollector:
        def collect_summary(self, period_hours: int) -> SummaryMetrics:
            assert period_hours == 24
            return SummaryMetrics(total_runs_24h=1)

    monkeypatch.setattr("phlo.cli.commands.metrics.get_metrics_collector", lambda: FakeCollector())
    result = CliRunner().invoke(metrics_group, ["summary"])
    assert result.exit_code == 0
    assert "Platform Metrics Summary" in result.output
    assert "Total:     1" in result.output


def test_metrics_summary_json_command(monkeypatch) -> None:
    class FakeCollector:
        def collect_summary(self, period_hours: int) -> SummaryMetrics:
            assert period_hours == 24
            return SummaryMetrics(total_runs_24h=1)

    monkeypatch.setattr("phlo.cli.commands.metrics.get_metrics_collector", lambda: FakeCollector())
    result = CliRunner().invoke(metrics_group, ["summary", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["errors"] == []
    assert payload["data"]["metrics"]["total_runs_24h"] == 1
