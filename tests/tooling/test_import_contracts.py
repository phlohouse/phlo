"""Import enforcement must reject bad edges, not merely pass today's tree."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import grimp
import pytest
from importlinter.configuration import configure
from importlinter.contracts.forbidden import ForbiddenContract
from importlinter.contracts.layers import LayersContract

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_import_cycles", ROOT / "scripts/check_import_cycles.py"
)
assert SPEC is not None
assert SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_ast_graph_includes_deferred_type_only_relative_and_reexport_edges(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("from sample.a import value\n", encoding="utf-8")
    (package / "a.py").write_text(
        "def value():\n    from . import b\n    return b\n", encoding="utf-8"
    )
    (package / "b.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import sample.a\n",
        encoding="utf-8",
    )
    graph = checker.import_graph((package,))
    assert graph == {"sample": {"sample.a"}, "sample.a": {"sample.b"}, "sample.b": {"sample.a"}}
    assert checker.strongly_connected_components(graph) == [["sample.a", "sample.b"]]
    assert checker.inward_aggregator_imports(graph, (package,)) == []
    (package / "b.py").write_text("from sample import value\n", encoding="utf-8")
    graph = checker.import_graph((package,))
    assert checker.inward_aggregator_imports(graph, (package,)) == [("sample.b", "sample")]
    assert checker.strongly_connected_components(graph) == [["sample", "sample.a", "sample.b"]]
    (package / "b.py").write_text("from sample.missing import value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing local module sample.missing"):
        checker.import_graph((package,))


@pytest.mark.parametrize(
    ("contract_name", "importer", "imported"),
    [
        (
            "Capability definitions do not depend on application composition",
            "phlo.capabilities.resolver",
            "phlo.plugins.discovery._plugin_loading",
        ),
        (
            "Capability definitions do not depend on application composition",
            "phlo.capabilities.interfaces",
            "phlo.helpers.io",
        ),
        (
            "Capability definitions do not depend on application composition",
            "phlo.capabilities.specs",
            "phlo.infrastructure.containers",
        ),
        ("Core never imports public aggregators inward", "phlo.helpers.io", "phlo"),
        ("Core never imports public aggregators inward", "phlo.helpers.io", "phlo.helpers"),
        ("Core never imports public aggregators inward", "phlo.helpers.io", "phlo.capabilities"),
        ("CLI is an outer layer", "phlo.infrastructure.containers", "phlo.cli.main"),
        (
            "Application capability and domain layering",
            "phlo.capabilities.specs",
            "phlo.application.discovery",
        ),
        (
            "Application capability and domain layering",
            "phlo.migrations.specs",
            "phlo.capabilities.resolver",
        ),
    ],
)
def test_live_contract_rejects_forbidden_edge(
    contract_name: str, importer: str, imported: str
) -> None:
    configure()
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "importlinter"
    ]
    contract_options = next(item for item in options["contracts"] if item["name"] == contract_name)
    # import-linter's configuration adapter normalises booleans to strings.
    raw = {
        key: str(value).lower() if isinstance(value, bool) else value
        for key, value in contract_options.items()
    }
    contract_type = LayersContract if raw["type"] == "layers" else ForbiddenContract
    contract = contract_type(contract_name, options, raw)
    graph = grimp.build_graph(*options["root_packages"], exclude_type_checking_imports=False)
    assert contract.check(graph, verbose=False).kept
    graph.add_import(
        importer=importer, imported=imported, line_number=1, line_contents="negative fixture"
    )
    assert not contract.check(graph, verbose=False).kept
