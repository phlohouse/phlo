"""Neutral evidence inputs to Dataset readiness.

Evidence is produced by executors, stored durably, and read by core. This
module defines only the neutral shapes and the capability interface
every evidence source must satisfy -- quality checks, governance surface
readings, and run evidence all arrive through it, never through a provider
import. Missing evidence is its own status and is never collapsed into a
failed or passing control.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

EVIDENCE_STATUS_PRESENT = "present"
EVIDENCE_STATUS_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One neutral evidence item about a table or Dataset.

    ``status`` is ``present`` when the executor produced the item and
    ``missing`` when the source knows the kind applies but holds no data; a
    source that returns nothing for a kind is treated the same as ``missing``.
    ``payload`` is JSON-serializable and provider-neutral.
    """

    kind: str
    subject: str
    status: str = EVIDENCE_STATUS_PRESENT
    payload: dict[str, Any] = field(default_factory=dict)
    source: str | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("evidence kind is required")
        if not self.subject:
            raise ValueError("evidence subject is required")
        if self.status not in {EVIDENCE_STATUS_PRESENT, EVIDENCE_STATUS_MISSING}:
            raise ValueError(f"Unknown evidence status: {self.status}")

    @property
    def is_missing(self) -> bool:
        return self.status == EVIDENCE_STATUS_MISSING


@runtime_checkable
class DatasetEvidenceSource(Protocol):
    """Capability interface for reading Dataset evidence.

    Implementations are registered provider-free (``dataset_evidence``
    capability family); core calls this protocol and never imports a provider.
    """

    def evidence(self, subject: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        """Return evidence records of the requested kinds for one subject."""
        ...
