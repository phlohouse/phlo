"""Boundary checks: the neutral Dataset core never imports a provider.

Also pins the capability families so evidence sources and durable state stores
are registered provider-free through the capability registry.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from phlo.capabilities.registry import CAPABILITY_FAMILIES, CapabilityRegistry
from phlo.capabilities.specs import (
    DatasetEvidenceSourceSpec,
    DatasetStateStoreSpec,
)

DATASET_CORE = Path("src/phlo/dataset")


def _imports(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _offending_imports(module: ast.Module) -> set[str]:
    """Return imports that reach outside the stdlib and the dataset core."""
    offenders: set[str] = set()
    for name in _imports(module):
        if name == "phlo.dataset" or name.startswith("phlo.dataset."):
            continue
        if name.split(".")[0] in sys.stdlib_module_names:
            continue
        offenders.add(name)
    return offenders


def test_dataset_core_imports_only_stdlib_and_itself() -> None:
    for path in DATASET_CORE.rglob("*.py"):
        offenders = _offending_imports(ast.parse(path.read_text(encoding="utf-8")))
        assert not offenders, f"{path} imports non-neutral modules: {sorted(offenders)}"


@pytest.mark.parametrize(
    "family,spec_type,provider_method",
    [
        ("dataset_evidence", DatasetEvidenceSourceSpec, "get_dataset_evidence_sources"),
        ("dataset_state_store", DatasetStateStoreSpec, "get_dataset_state_stores"),
    ],
)
def test_dataset_capability_families_are_registered_provider_free(
    family: str, spec_type: type, provider_method: str
) -> None:
    definition = CAPABILITY_FAMILIES[family]
    assert definition.spec_type is spec_type
    assert definition.provider_method == provider_method
    spec = spec_type(name=f"test-{family}", provider=object())
    # Discovery in other modules may populate the process-global registry.
    registry = CapabilityRegistry()
    registry.register(family, spec)
    assert registry.list(family) == [spec]


def test_dataset_state_store_namespace_is_project_scoped() -> None:
    from phlo.dataset import state_store_namespace

    namespace = state_store_namespace("/projects/demo")
    assert namespace.startswith("observatory.dataset_workflow.")
    assert namespace == state_store_namespace("/projects/demo")
    assert namespace != state_store_namespace("/projects/other")
