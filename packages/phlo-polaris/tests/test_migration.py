"""Tests for the Nessie-to-Polaris migration (dry-run by default)."""

from __future__ import annotations

from types import SimpleNamespace

from phlo_polaris.migration import MigrationPlanEntry, import_tables, plan_migration


class FakeSourceCatalog:
    def __init__(self) -> None:
        self.loaded: list[tuple] = []

    def list_namespaces(self) -> list[tuple]:
        return [("bronze",), ("silver",)]

    def list_tables(self, namespace: str) -> list[tuple]:
        if namespace == "bronze":
            return [("bronze", "events")]
        return [("silver", "orders")]

    def load_table(self, identifier: tuple) -> SimpleNamespace:
        self.loaded.append(identifier)
        return SimpleNamespace(
            location=f"s3://lake/{identifier[-1]}",
            current_snapshot_id=7,
            metadata_location=f"s3://lake/{identifier[-1]}/metadata/00000-7.metadata.json",
        )


class FakeTargetCatalog:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []

    def create_namespace_if_not_exists(self, namespace: str) -> None:
        pass

    def register_table(self, table_name: str, metadata_location: str) -> None:
        self.registered.append((table_name, metadata_location))


def test_plan_migration_inventories_all_tables() -> None:
    source = FakeSourceCatalog()
    entries = plan_migration(source_catalog=source)
    assert [(entry.namespace, entry.table_name) for entry in entries] == [
        ("bronze", "events"),
        ("silver", "orders"),
    ]


def test_import_registers_tables_without_touching_source_data() -> None:
    source = FakeSourceCatalog()
    target = FakeTargetCatalog()
    entries = [
        MigrationPlanEntry(namespace="bronze", table_name="events", location="s3://lake/events")
    ]

    results = import_tables(entries, source_catalog=source, target_catalog=target)

    assert results == [{"namespace": "bronze", "table": "events", "registered": True}]
    assert target.registered == [("events", "s3://lake/events/metadata/00000-7.metadata.json")]
    # Loading metadata is read-only against the source.
    assert source.loaded == [("bronze", "events")]
