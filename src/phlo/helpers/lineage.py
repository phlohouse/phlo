"""Lineage helper utilities.

Lineage is collected as input/output table sets and emitted as a
cartesian product of edges through the resolved lineage-sink capability;
emission is a silent no-op when no sink is available. Also owns the
standard _phlo_* metadata column names shared across the pipeline.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from phlo.capabilities import resolve_capability

PHLO_ROW_ID_COLUMN = "_phlo_row_id"
PHLO_RUN_ID_COLUMN = "_phlo_run_id"
PHLO_INGESTED_AT_COLUMN = "_phlo_ingested_at"
PHLO_PARTITION_COLUMN = "_phlo_partition_date"


@dataclass(slots=True)
class LineageCollector:
    """Collect input/output table edges for a workflow block."""

    inputs: set[str] = field(default_factory=set)
    outputs: set[str] = field(default_factory=set)

    def input(self, table: str) -> None:
        """Record an input table."""
        self.inputs.add(table)

    def output(self, table: str) -> None:
        """Record an output table."""
        self.outputs.add(table)

    def edges(self) -> list[tuple[str, str]]:
        """Return all input-to-output lineage edges."""
        return [
            (source, target) for source in sorted(self.inputs) for target in sorted(self.outputs)
        ]


def emit_input_output_lineage(
    inputs: list[str],
    outputs: list[str],
    *,
    asset_keys: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    sink: Any = None,
) -> int:
    """Emit lineage edges through the active lineage sink when available."""
    provider = sink
    if provider is None:
        resolution = resolve_capability("lineage_sink")
        provider = resolution.provider if resolution else None
    if provider is None or not hasattr(provider, "record_asset_edges"):
        return 0
    edges = [(source, target) for source in inputs for target in outputs]
    return int(provider.record_asset_edges(edges, asset_keys=asset_keys, metadata=metadata))


@contextmanager
def lineage_context(*, sink: Any = None, metadata: dict[str, Any] | None = None):
    """Collect lineage in a block and emit it on success."""
    collector = LineageCollector()
    yield collector
    emit_input_output_lineage(
        list(collector.inputs),
        list(collector.outputs),
        metadata=metadata,
        sink=sink,
    )


def row_id_columns() -> dict[str, str]:
    """Return standard Phlo lineage metadata column names."""
    return {
        "row_id": PHLO_ROW_ID_COLUMN,
        "run_id": PHLO_RUN_ID_COLUMN,
        "ingested_at": PHLO_INGESTED_AT_COLUMN,
        "partition": PHLO_PARTITION_COLUMN,
    }


def lineage_summary(edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Summarize lineage edges into upstream/downstream table lists."""
    return {
        "upstream": sorted({source for source, _ in edges}),
        "downstream": sorted({target for _, target in edges}),
    }
