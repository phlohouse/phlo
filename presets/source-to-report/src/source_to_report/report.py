"""Render the pack's report from strict facts and pre-drawn charts.

The renderer owns three things the domain should not have to: Jinja's strict
undefined mode (a missing fact fails the build), autoescaping, and the check
that the finished page is self-contained.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from source_to_report import domain
from source_to_report.facts import Facts

DOMAIN_DIR = Path(__file__).resolve().parent / "domain"


def _number(value: Any, digits: int = 0) -> str:
    return f"{float(value):,.{digits}f}"


def _percent(value: Any, digits: int = 0) -> str:
    return f"{float(value) * 100:,.{digits}f}%"


def _environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(DOMAIN_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["n0"] = lambda value: _number(value, 0)
    environment.filters["n1"] = lambda value: _number(value, 1)
    environment.filters["n2"] = lambda value: _number(value, 2)
    environment.filters["pct"] = lambda value: _percent(value, 0)
    environment.filters["month_label"] = lambda value: date.fromisoformat(str(value)).strftime(
        "%B %Y"
    )
    environment.filters["date_label"] = lambda value: _date_label(value)
    return environment


def _date_label(value: Any) -> str:
    day = date.fromisoformat(str(value))
    return f"{day.day} {day:%B %Y}"


def _check_self_contained(text: str) -> None:
    """Refuse a report that needs the network to render."""
    forbidden = ("<script", "<link", 'src="http', 'href="http', "url(http")
    for marker in forbidden:
        if marker in text:
            raise RuntimeError(f"report is not self-contained: found {marker!r}")


def render(
    out_dir: Path,
    *,
    facts: Facts,
    charts: dict[str, str],
    provenance: dict[str, Any],
) -> Path:
    """Render ``index.html`` and return its path."""
    template = _environment().get_template("report.html.j2")
    failed = [check for check in facts.checks() if not check.get("ok")]
    text = template.render(
        facts=facts,
        charts=charts,
        provenance=provenance,
        domain=domain,
        failed_checks=failed,
    )
    _check_self_contained(text)
    path = out_dir / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
