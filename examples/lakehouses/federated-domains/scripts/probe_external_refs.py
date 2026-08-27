"""Boundary probe: cross-provider external-reference validation.

Demonstrates issue #795 semantics end to end against this example's three
domain dbt projects:

1. ``build_dbt_asset_specs()`` records every dep that no dbt project
   produces (the ``dlt_`` ingestion bindings) as ``phlo/external_deps``
   metadata.
2. The aggregation-point validator
   (``phlo.capabilities.external_refs.validate_external_asset_references``)
   warns for each missing key when only dbt specs are registered.
3. The same validator is silent once the phlo-dlt ingestion assets are part
   of the graph: every external reference resolves.

Usage::

    DBT_PROJECT_DIRS=workflows/sales/transforms/dbt,workflows/finance/transforms/dbt,\\
workflows/operations/transforms/dbt \\
    DBT_NAMESPACED_ASSET_KEYS=1 \\
    uv run python scripts/probe_external_refs.py
"""

from __future__ import annotations

import logging
import os
import pathlib

EXAMPLE = pathlib.Path(__file__).resolve().parents[1]
os.environ.setdefault("PHLO_PROJECT_PATH", str(EXAMPLE))
os.environ.setdefault(
    "DBT_PROJECT_DIRS",
    ",".join(
        [
            "workflows/sales/transforms/dbt",
            "workflows/finance/transforms/dbt",
            "workflows/operations/transforms/dbt",
        ]
    ),
)
os.environ.setdefault("DBT_NAMESPACED_ASSET_KEYS", "1")
os.chdir(EXAMPLE)


class CompactWarnings(logging.Formatter):
    """Render phlo structured warnings as short key=value lines."""

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict) and "event" in record.msg:
            fields = record.msg
            parts = [f"WARNING {fields['event']}"]
            for key in ("asset_key", "referenced_key", "dbt_project"):
                if fields.get(key):
                    parts.append(f"{key}={fields[key]}")
            return " ".join(parts)
        return record.getMessage()


handler = logging.StreamHandler()
handler.setFormatter(CompactWarnings())
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.WARNING)

import phlo.capabilities.external_refs as external_refs  # noqa: E402 - env setup must run first
from phlo.capabilities.specs import AssetSpec  # noqa: E402
from phlo_dbt.assets import build_dbt_asset_specs  # noqa: E402

print("== building dbt specs from 3 domain projects ==")
specs = build_dbt_asset_specs()
for spec in sorted(specs, key=lambda s: s.key):
    ext = spec.metadata.get("phlo/external_deps", [])
    print(f"  {spec.key:38s} deps={spec.deps}")
    print(f"    external={ext}")

print()
print("== validator: dbt-only graph (dlt assets NOT registered) ==")
external_refs.validate_external_asset_references(specs)
print("(validator finished)")

print()
print("== validator: full graph with phlo-dlt assets registered ==")
dlt_specs = [
    AssetSpec(key="dlt_finance_invoices", group="bronze", description=None),
    AssetSpec(key="dlt_sales_deals", group="bronze", description=None),
    AssetSpec(key="dlt_operations_incidents", group="bronze", description=None),
]
external_refs.validate_external_asset_references([*specs, *dlt_specs])
print("(validator finished - silent: every external reference resolves)")
print()
print("PROBE OK")
