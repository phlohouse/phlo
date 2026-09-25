"""Check PR selection against asymmetric workspace changes."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "select_ci", Path(__file__).resolve().parents[2] / "scripts/select_ci.py"
)
assert SPEC and SPEC.loader
select_ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(select_ci)
select = select_ci.select


def test_docs_only_skips_package_groups() -> None:
    selection = select({"docs/getting-started.md", "README.md"})
    assert selection == {
        "groups": [],
        "python": False,
        "frontend": False,
        "writer": False,
        "integration": False,
    }


def test_trino_selects_reverse_dependency_groups() -> None:
    selection = select({"packages/phlo-trino/src/phlo_trino/plugin.py"})
    # Nessie depends on Trino; Iceberg and Dagster depend on Nessie.
    assert {entry["group"] for entry in selection["groups"]} == {
        "data",
        "interfaces",
        "platform",
        "runtime",
    }
    assert selection["python"] is True
    assert selection["integration"] is True


def test_unknown_config_and_empty_diff_run_everything() -> None:
    for paths in ({"scripts/select_ci.py"}, set()):
        selection = select(paths)
        assert {entry["group"] for entry in selection["groups"]} == {
            "platform",
            "data",
            "runtime",
            "interfaces",
        }
        assert selection["frontend"] is True
        assert selection["writer"] is True


def test_writer_only_skips_python_and_package_groups() -> None:
    selection = select({"apps/phlo-github-writer/src/index.ts"})
    assert selection["groups"] == []
    assert selection["python"] is False
    assert selection["writer"] is True


def test_leaf_package_does_not_run_unrelated_groups() -> None:
    selection = select({"packages/phlo-traefik/src/phlo_traefik/plugin.py"})
    assert [entry["group"] for entry in selection["groups"]] == ["platform"]
