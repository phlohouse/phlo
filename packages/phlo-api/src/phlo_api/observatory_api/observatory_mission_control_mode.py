"""Data mode, project identity and read-evidence helpers for Mission Control.

``PHLO_OBSERVATORY_DATA_MODE`` selects ``live`` (default) or ``demo``. Demo is a
development/test mode: seed collections are served, every response is labelled
``demo``, and provider mutations are rejected. In live mode nothing falls back
to seeds — an absent collection is reported as it is, and a failed dependency
produces a typed unavailable outcome instead of fabricated data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Generic, Literal, NoReturn, TypeVar, cast

import yaml
from fastapi import HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    ReadEvidence,
    ReasonCode,
)

DATA_MODE_ENV = "PHLO_OBSERVATORY_DATA_MODE"
ENVIRONMENT_ENV = "PHLO_ENVIRONMENT"
DataMode = Literal["live", "demo"]


def data_mode() -> DataMode:
    """Return the configured data mode; anything but explicit ``demo`` is live."""
    return "demo" if os.environ.get(DATA_MODE_ENV, "").strip().lower() == "demo" else "live"


def environment_id() -> str:
    """Return the one configured environment identity for this deployment.

    One deployment serves exactly one environment; ``PHLO_ENVIRONMENT`` is the
    same source the security mode and settings configuration read.
    """
    return os.environ.get(ENVIRONMENT_ENV, "dev").strip().lower() or "dev"


def project_root() -> Path:
    """Resolve the project root the same way the rest of Observatory does."""
    return Path(os.environ.get("PHLO_PROJECT_PATH", Path.cwd())).resolve()


def _read_project_config(root: Path) -> dict[str, Any] | None:
    """Parse ``phlo.yaml`` into a mapping, or None when absent/malformed."""
    path = root / "phlo.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_project() -> tuple[bool, str | None]:
    """Validate that ``PHLO_PROJECT_PATH`` names a real Phlo project.

    A valid project is a directory containing a ``phlo.yaml`` that parses to a
    mapping with a ``name``. Returns ``(valid, detail)`` where ``detail`` is a
    sanitized reason suitable for the mission context diagnostic.
    """
    root = project_root()
    if not root.is_dir():
        return False, "PHLO_PROJECT_PATH does not name an existing directory"
    path = root / "phlo.yaml"
    if not path.is_file():
        return False, "phlo.yaml not found under PHLO_PROJECT_PATH"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return False, "phlo.yaml is not valid YAML"
    except OSError:
        return False, "phlo.yaml cannot be read"
    if not isinstance(payload, dict):
        return False, "phlo.yaml does not parse to a mapping"
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "phlo.yaml has no project name"
    return True, None


@lru_cache(maxsize=8)
def _project_name_for(root: str) -> str:
    payload = _read_project_config(Path(root))
    if payload is not None:
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return Path(root).name


def project_id() -> str:
    """Return the configured project's non-secret identity."""
    return _project_name_for(str(project_root()))


SourceStatus = Literal["ok", "absent", "unavailable", "unsupported"]
T = TypeVar("T")


@dataclass(slots=True)
class SourceOutcome(Generic[T]):
    """Typed result from a live substrate derivation.

    ``ok`` carries the source's actual answer — including an empty collection,
    which is a truthful result and never falls through to seed data.
    ``absent`` means the requested resource does not exist. ``unavailable``
    means a required dependency failed. ``unsupported`` means no provider or
    producer exists for the surface.
    """

    status: SourceStatus
    data: T | None = None
    detail: str | None = None
    source: str | None = None
    # Set when ``data`` was served from a persisted read-model entry after the
    # live source failed. ``last_confirmed_at`` is that fetch's wall-clock time.
    stale: bool = False
    last_confirmed_at: str | None = None


def ok(
    data: T,
    *,
    source: str | None = None,
    stale: bool = False,
    last_confirmed_at: str | None = None,
) -> SourceOutcome[T]:
    return SourceOutcome(
        status="ok",
        data=data,
        source=source,
        stale=stale,
        last_confirmed_at=last_confirmed_at,
    )


def absent(detail: str | None = None, *, source: str | None = None) -> SourceOutcome[T]:
    return SourceOutcome(status="absent", detail=detail, source=source)


def unavailable(detail: str | None = None, *, source: str | None = None) -> SourceOutcome[T]:
    return SourceOutcome(status="unavailable", detail=detail, source=source)


def unsupported(detail: str | None = None, *, source: str | None = None) -> SourceOutcome[T]:
    return SourceOutcome(status="unsupported", detail=detail, source=source)


# ------------------------------------------------------------------ evidence


def _now() -> str:
    return datetime.now(UTC).isoformat()


def evidence(
    status: Literal["live", "stale", "unavailable", "unsupported", "demo"],
    *,
    source: str,
    reason_code: ReasonCode | None = None,
    detail: str | None = None,
    dropped_records: int = 0,
    last_confirmed_at: str | None = None,
) -> ReadEvidence:
    return ReadEvidence(
        status=status,
        project_id=project_id(),
        environment_id=environment_id(),
        source=source,
        observed_at=_now(),
        last_confirmed_at=last_confirmed_at,
        reason_code=reason_code,
        detail=detail,
        dropped_records=dropped_records,
    )


def envelope(data: T | None, ev: ReadEvidence) -> ReadEnvelope[T]:
    return ReadEnvelope(data=data, evidence=ev)


def live_envelope(
    data: T | None,
    *,
    source: str,
    dropped_records: int = 0,
    detail: str | None = None,
    stale: bool = False,
    last_confirmed_at: str | None = None,
) -> ReadEnvelope[T]:
    return envelope(
        data,
        evidence(
            "stale" if stale else "live",
            source=source,
            dropped_records=dropped_records,
            reason_code="partial" if dropped_records else ("stale" if stale else None),
            detail=detail,
            last_confirmed_at=last_confirmed_at,
        ),
    )


def demo_envelope(data: T | None, *, source: str = "seed") -> ReadEnvelope[T]:
    return envelope(
        data,
        evidence("demo", source=source, reason_code="demo"),
    )


def unsupported_envelope(detail: str, *, source: str = "none") -> ReadEnvelope[list]:
    return envelope(
        [],
        evidence(
            "unsupported",
            source=source,
            reason_code="no_producer",
            detail=detail,
        ),
    )


def raise_unavailable(detail: str) -> NoReturn:
    """503 with a sanitized problem detail for a failed required dependency."""
    raise HTTPException(status_code=503, detail=detail)


def raise_absent(detail: str) -> NoReturn:
    raise HTTPException(status_code=404, detail=detail)


def raise_unsupported(detail: str) -> NoReturn:
    """501: the surface has no provider or producer for this project."""
    raise HTTPException(status_code=501, detail=detail)


def outcome_data(outcome: SourceOutcome[T]) -> T:
    """Return the data for ``ok`` outcomes; raise the matching HTTP status otherwise.

    ``absent`` -> 404, ``unavailable`` -> 503, ``unsupported`` -> 501. Each is a
    distinct observable outcome, never a silent empty result.
    """
    if outcome.status == "absent":
        raise_absent(outcome.detail or "Not found")
    if outcome.status == "unavailable":
        raise_unavailable(outcome.detail or "A required dependency is unavailable")
    if outcome.status == "unsupported":
        raise_unsupported(outcome.detail or "Not supported for this project")
    return cast(T, outcome.data)


def reject_demo_mutations() -> None:
    """Refuse provider mutations while the server runs in demo data mode."""
    if data_mode() == "demo":
        raise HTTPException(
            status_code=409,
            detail="Observatory is in demo data mode; provider mutations are disabled.",
        )
