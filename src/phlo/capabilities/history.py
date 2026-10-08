"""Define immutable history policy and count-carrying write failures.

History providers compare explicit payload fields or a supplied hash, never
arrival metadata. Ambiguous commits carry reconciliation, not insertion counts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _column_name(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and not value.startswith(("_phlo_", "_dlt_"))
    )


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    """Declare entity-scoped version identity and explicit payload comparison."""

    entity_key: str
    version_key: str
    payload_columns: tuple[str, ...] | None = None
    payload_hash_column: str | None = None

    def __post_init__(self) -> None:
        if not _column_name(self.entity_key) or not _column_name(self.version_key):
            raise ValueError("History entity_key and version_key must name non-metadata columns")
        if self.entity_key == self.version_key:
            raise ValueError("History entity_key and version_key must be distinct")
        if (self.payload_columns is None) == (self.payload_hash_column is None):
            raise ValueError(
                "History requires exactly one of payload_columns or payload_hash_column"
            )
        if self.payload_columns is not None:
            if not isinstance(self.payload_columns, (list, tuple)) or not self.payload_columns:
                raise ValueError("History payload_columns must be a nonempty list of column names")
            if not all(_column_name(name) for name in self.payload_columns):
                raise ValueError("History payload_columns must exclude arrival metadata columns")
            if len(set(self.payload_columns)) != len(self.payload_columns):
                raise ValueError("History payload_columns must not contain duplicates")
            object.__setattr__(self, "payload_columns", tuple(sorted(self.payload_columns)))
        elif not _column_name(self.payload_hash_column):
            raise ValueError("History payload_hash_column must name a non-metadata column")

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> HistoryPolicy:
        """Reject missing and unknown history options at the configuration boundary."""
        try:
            return cls(**(config or {}))
        except TypeError as exc:
            raise ValueError(
                "History config requires entity_key, version_key and payload_columns "
                "or payload_hash_column; no deduplication or ordering options are supported"
            ) from exc

    @property
    def comparison_columns(self) -> tuple[str, ...]:
        """Return the explicitly selected payload fields in canonical order."""
        return self.payload_columns or (str(self.payload_hash_column),)


class HistoryWriteError(RuntimeError):
    """Carry definite write metrics or ambiguous-outcome reconciliation to evidence."""

    def __init__(
        self,
        message: str,
        *,
        metrics: dict[str, int],
        reconciliation: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.metrics = metrics
        self.reconciliation = reconciliation


class HistoryConflictError(HistoryWriteError):
    """Reject conflicting immutable versions before publishing any mutation."""


class HistoryCommitUnknownError(HistoryWriteError):
    """Require explicit recovery after a commit with an ambiguous outcome."""
