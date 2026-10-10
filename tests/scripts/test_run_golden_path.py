"""Tests for the golden path script's phase helpers.

The live workflow needs Docker, so these cover the pure steps it is built
from: argument expansion, port resolution, workflow file generation,
preflight parsing, and the OpenMetadata lineage and lineage-export readers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_golden_path", REPO_ROOT / "scripts" / "run_golden_path.py"
)
assert _spec and _spec.loader
run_golden_path = importlib.util.module_from_spec(_spec)
sys.modules["run_golden_path"] = run_golden_path
_spec.loader.exec_module(run_golden_path)


def _run(tmp_path: Path, *argv: str) -> object:
    args = run_golden_path.parse_args(["--project-dir", str(tmp_path / "project"), *argv])
    return run_golden_path._new_run(args)


def test_test_all_enables_every_optional_test() -> None:
    args = run_golden_path.parse_args(["--test-all"])

    assert all(getattr(args, flag) for flag in run_golden_path._OPTIONAL_TEST_FLAGS)


def test_project_dir_names_the_project(tmp_path: Path) -> None:
    run = _run(tmp_path)

    assert run.project_name == "project"
    assert run.project_dir == tmp_path / "project"
    assert run.phlo_dir == tmp_path / "project" / ".phlo"


def test_resolve_ports_covers_requested_services_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applied: dict[str, str] = {}
    monkeypatch.setattr(run_golden_path, "resolve_port", lambda name, default: default + 1)
    monkeypatch.setattr(
        run_golden_path, "apply_env_updates", lambda phlo_dir, updates: applied.update(updates)
    )
    run = _run(tmp_path, "--mode", "pypi", "--test-superset")

    run_golden_path._resolve_ports(run)

    assert applied == run.resolved_ports
    assert applied["PHLO_API_PORT"] == "54001"
    assert applied["DAGSTER_PORT"] == "3001"
    assert applied["SUPERSET_PORT"] == "8089"
    assert "HASURA_PORT" not in applied
    assert "PHLO_DEV_EXTRA_PACKAGES" not in applied


def test_resolve_ports_points_lineage_at_project_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_golden_path, "resolve_port", lambda name, default: default)
    monkeypatch.setattr(run_golden_path, "apply_env_updates", lambda phlo_dir, updates: None)
    monkeypatch.delenv("LINEAGE_DB_URL", raising=False)
    run = _run(tmp_path, "--test-lineage")
    run.env_vars = {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "d"}

    run_golden_path._resolve_ports(run)

    assert run.resolved_ports["LINEAGE_DB_URL"] == "postgresql://u:p@postgres:5432/d"
    assert run.resolved_ports["PHLO_DEV_EXTRA_PACKAGES"] == "phlo-lineage"
    assert run.lineage_db_url_host == "postgresql://u:p@localhost:5432/d"


def test_write_workflow_files_uses_project_trino_settings(tmp_path: Path) -> None:
    env_vars = {"TRINO_USER": "analyst", "TRINO_CATALOG": "lake", "TRINO_SCHEMA": "bronze"}

    run_golden_path._write_workflow_files(tmp_path, env_vars)

    dbt = tmp_path / "workflows" / "transforms" / "dbt"
    profiles = (dbt / "profiles" / "profiles.yml").read_text(encoding="utf-8")
    assert "user: analyst" in profiles
    assert "catalog: lake" in profiles
    sources = (dbt / "models" / "sources" / "raw.yml").read_text(encoding="utf-8")
    assert "database: lake" in sources
    assert "schema: bronze" in sources
    asset = tmp_path / "workflows" / "ingestion" / "jsonplaceholder" / "posts.py"
    assert "validate=False" in asset.read_text(encoding="utf-8")
    assert (tmp_path / "workflows" / "publishing" / "jsonplaceholder.py").is_file()


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("Filesystem 1G-blocks Used Available Use% Mounted\n/dev 100G 20G 80G 20% /tmp\n", 80),
        ("Filesystem\n", None),
        ("Filesystem 1G-blocks Used Available\n/dev 100G 20G lots\n", None),
    ],
)
def test_available_tmp_gb_parses_df(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: int | None
) -> None:
    monkeypatch.setattr(
        run_golden_path.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=stdout)
    )

    assert run_golden_path._available_tmp_gb() == expected


def test_busy_ports_fail_preflight_without_auto_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_golden_path, "check_port_in_use", lambda port: port == 3000)

    assert run_golden_path._check_common_ports(auto_cleanup=False) is False
    assert run_golden_path._check_common_ports(auto_cleanup=True) is True


def test_lineage_edge_matches_by_id_or_node_name() -> None:
    table = {"id": "mart-id"}
    by_id = {"edges": [{"fromEntity": "src-id", "toEntity": {"id": "mart-id"}}]}
    by_node_name = {
        "upstreamEdges": [{"fromEntity": "n1", "toEntity": "n2"}],
        "nodes": [
            {"id": "n1", "fullyQualifiedName": "svc.db.raw.posts"},
            {"id": "n2", "name": "svc.db.raw_marts.posts_mart"},
        ],
    }
    unrelated = {"edges": [{"fromEntity": "other", "toEntity": "mart-id"}, "not-an-edge"]}

    def has_edge(lineage: object, source_id: object = None) -> bool:
        return run_golden_path._lineage_has_edge(
            lineage,
            source_id=source_id,
            source_fqn="svc.db.raw.posts",
            table=table,
            table_fqn="svc.db.raw_marts.posts_mart",
        )

    assert has_edge(by_id, source_id="src-id")
    assert has_edge(by_node_name)
    assert not has_edge(unrelated, source_id="src-id")
    assert not has_edge(None)


def test_summarize_lineage_export_counts_assets_and_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(run_golden_path, "log_info", messages.append)
    export = tmp_path / "lineage.json"
    export.write_text(
        json.dumps({"assets": {"a": {}, "b": {}}, "edges": {"a": ["b"], "b": []}}),
        encoding="utf-8",
    )

    run_golden_path._summarize_lineage_export(export)
    export.write_text("not json", encoding="utf-8")
    run_golden_path._summarize_lineage_export(export)

    assert messages[0] == "  Lineage graph: 2 assets, 1 edges"
    assert messages[1].startswith("  Lineage export parse failed:")
