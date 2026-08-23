"""Tests the lineage CLI: asset name resolution against lineage graph
nodes and the show-lineage output."""

from click.testing import CliRunner
from phlo_lineage.cli_lineage import _resolve_asset_name, lineage_group, show_lineage
from phlo_lineage.graph import LineageGraph


def test_resolve_asset_name_maps_dlt_asset_to_raw_table_node() -> None:
    graph = LineageGraph()
    graph.add_asset("raw.orders")

    resolved_name, matches = _resolve_asset_name(graph, "dlt_orders")

    assert resolved_name == "raw.orders"
    assert matches == ["raw.orders"]


def test_resolve_asset_name_keeps_ambiguous_dlt_table_matches_unresolved() -> None:
    graph = LineageGraph()
    graph.add_asset("raw.orders")
    graph.add_asset("archive.orders")

    resolved_name, matches = _resolve_asset_name(graph, "dlt_orders")

    assert resolved_name is None
    assert sorted(matches) == ["archive.orders", "raw.orders"]


def test_show_lineage_renders_direction_labels(monkeypatch) -> None:
    graph = LineageGraph()
    graph.add_edge("raw.orders", "stg_orders")
    monkeypatch.setattr("phlo_lineage.cli_lineage.get_lineage_graph", lambda: graph)

    result = CliRunner().invoke(show_lineage, ["dlt_orders", "--direction", "both"])

    assert result.exit_code == 0
    assert "[downstream]" in result.output


def test_column_upstream_reports_database_failure(monkeypatch) -> None:
    class FailingStore:
        def __init__(self, _connection_string: str) -> None:
            pass

        def get_upstream_columns(self, *_args, **_kwargs):
            raise RuntimeError("database down")

    monkeypatch.setattr(
        "phlo_lineage.store.resolve_lineage_db_url_with_postgres_fallback",
        lambda: "postgresql://lineage",
    )
    monkeypatch.setattr("phlo_lineage.store.LineageStore", FailingStore)

    result = CliRunner().invoke(lineage_group, ["column", "upstream", "missing_asset"])

    assert result.exit_code != 0
    assert "could not query column lineage" in result.output
    assert "Check that the lineage database is running and reachable." in result.output
    assert "Run: phlo services status postgres" in result.output
    assert "Traceback" not in result.output


def test_column_downstream_reports_database_failure(monkeypatch) -> None:
    class FailingStore:
        def __init__(self, _connection_string: str) -> None:
            pass

        def get_downstream_columns(self, *_args, **_kwargs):
            raise RuntimeError("database down")

    monkeypatch.setattr(
        "phlo_lineage.store.resolve_lineage_db_url_with_postgres_fallback",
        lambda: "postgresql://lineage",
    )
    monkeypatch.setattr("phlo_lineage.store.LineageStore", FailingStore)

    result = CliRunner().invoke(lineage_group, ["column", "downstream", "missing_asset"])

    assert result.exit_code != 0
    assert "could not query column lineage" in result.output
    assert "Check that the lineage database is running and reachable." in result.output
    assert "Run: phlo services status postgres" in result.output
    assert "Traceback" not in result.output
