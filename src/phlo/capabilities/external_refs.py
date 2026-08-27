"""Validation of cross-provider asset references in merged capability specs.

Asset providers may reference assets they do not own. The dbt provider, for
example, binds sources to other providers' assets (such as phlo-dlt
ingestion assets) through ``meta.phlo_asset_key``, so a dbt model's deps can
contain keys no dbt manifest produces. Individual providers cannot know
whether such a key exists elsewhere in the graph: they only see their own
specs.

Providers therefore record keys they depend on but cannot resolve in the
``phlo/external_deps`` spec metadata entry. Once every provider has
registered its specs and the complete asset-key set is available, this module
validates those recorded references. Validation happens at the capability
aggregation point (see ``phlo_dagster.framework.discovery``), not inside any
provider, so the check stays provider-neutral.

Example:
    >>> from phlo.capabilities.specs import AssetSpec
    >>> from phlo.capabilities.external_refs import validate_external_asset_references
    >>> spec = AssetSpec(
    ...     key="finance.invoice_aging",
    ...     group="gold",
    ...     description=None,
    ...     deps=["dlt_invoices", "dlt_missing"],
    ...     metadata={"phlo/external_deps": ["dlt_invoices", "dlt_missing"]},
    ... )
    >>> validate_external_asset_references([spec])  # logs dlt_missing
"""

from __future__ import annotations

from collections.abc import Iterable

from phlo.capabilities.specs import AssetSpec
from phlo.logging import get_logger

logger = get_logger(__name__)

EXTERNAL_DEPS_METADATA_KEY = "phlo/external_deps"


def validate_external_asset_references(assets: Iterable[AssetSpec]) -> None:
    """Warn when specs reference asset keys that no registered spec provides.

    Inspects the ``phlo/external_deps`` metadata entry on each spec (a list
    of asset keys the provider depends on but could not resolve against its
    own specs) and compares each entry against every registered asset key.
    Emits one structured warning per missing (referencing spec, referenced
    key) pair; duplicates across specs of the same pair are collapsed.

    Providers that resolve all their dependencies internally carry no
    ``phlo/external_deps`` metadata and cost nothing here.
    """
    specs = list(assets)
    known_keys = {spec.key for spec in specs}
    warned: set[tuple[str, str]] = set()
    for spec in specs:
        refs = spec.metadata.get(EXTERNAL_DEPS_METADATA_KEY)
        if refs is None:
            continue
        if not isinstance(refs, (list, tuple)):
            logger.warning(
                "asset_external_deps_metadata_malformed",
                asset_key=spec.key,
                expected="list of asset key strings",
            )
            continue
        owner = spec.metadata.get("dbt_project")
        for ref in refs:
            ref_key = str(ref)
            if ref_key in known_keys or (spec.key, ref_key) in warned:
                continue
            warned.add((spec.key, ref_key))
            logger.warning(
                "asset_external_reference_unresolved",
                asset_key=spec.key,
                referenced_key=ref_key,
                dbt_project=owner,
                hint=(
                    "No registered asset provides this key in any provider; "
                    "check the referencing source's meta.phlo_asset_key and "
                    "that the owning workflow is registered."
                ),
            )
