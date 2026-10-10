"""Tests phlo_iceberg.tables.remove_orphan_files: dry-run orphan discovery over
a local object store honours the retention window and in-use files."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyarrow.fs import LocalFileSystem, SubTreeFileSystem

from phlo_iceberg import resource as resource_module
from phlo_iceberg import tables

TABLE_LOCATION = "s3://bucket/warehouse/raw/events"
DATA_PREFIX = "bucket/warehouse/raw/events/data"
DAY = 24 * 60 * 60


class _LocalObjectStoreIO:
    """FileIO stand-in that serves s3:// locations from a local directory."""

    def __init__(self, root: Path) -> None:
        self.properties: dict[str, str] = {}
        self._filesystem = SubTreeFileSystem(str(root), LocalFileSystem())

    def parse_location(self, location: str, properties: dict[str, str]) -> tuple[str, str, str]:
        scheme, _, rest = location.partition("://")
        return scheme, rest.split("/", 1)[0], rest

    def fs_by_scheme(self, scheme: str, netloc: str) -> SubTreeFileSystem:
        return self._filesystem


class _Table:
    def __init__(self, io: _LocalObjectStoreIO, snapshot_files: list[list[str]]) -> None:
        self.io = io
        self._snapshot_files = snapshot_files

    def snapshots(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(manifests=lambda io, files=files, i=i: [self._manifest(i, files)])
            for i, files in enumerate(self._snapshot_files)
        ]

    def location(self) -> str:
        return TABLE_LOCATION

    @staticmethod
    def _manifest(index: int, files: list[str]) -> SimpleNamespace:
        entries = [SimpleNamespace(data_file=SimpleNamespace(file_path=path)) for path in files]
        return SimpleNamespace(
            manifest_path=f"{TABLE_LOCATION}/metadata/{index}.avro",
            fetch_manifest_entry=lambda io: entries,
        )


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Return a writer for data files and install a table over the local store."""
    (tmp_path / DATA_PREFIX).mkdir(parents=True)
    state = {"snapshots": []}

    def write(name: str, age_days: float) -> str:
        path = tmp_path / DATA_PREFIX / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
        mtime = time.time() - age_days * DAY
        os.utime(path, (mtime, mtime))
        return f"{DATA_PREFIX}/{name}"

    def reference(*names: str) -> None:
        state["snapshots"].append([f"{TABLE_LOCATION}/data/{name}" for name in names])

    table = _Table(_LocalObjectStoreIO(tmp_path), state["snapshots"])
    catalog = SimpleNamespace(load_table=lambda table_name: table)
    refs: list[str] = []
    monkeypatch.setattr(tables, "get_catalog", lambda ref: refs.append(ref) or catalog)
    return SimpleNamespace(write=write, reference=reference, refs=refs, root=tmp_path)


def test_reports_only_unreferenced_files_older_than_the_default_window(store) -> None:
    store.write("current.parquet", age_days=30)
    store.write("previous.parquet", age_days=30)
    old_orphan = store.write("orphan-old.parquet", age_days=8)
    store.write("orphan-recent.parquet", age_days=6)
    store.reference("previous.parquet")
    store.reference("current.parquet")

    result = tables.remove_orphan_files("raw.events")

    assert result == {"orphan_count": 1, "orphan_files": [old_orphan], "dry_run": True}


def test_older_than_days_widens_the_retention_window(store) -> None:
    store.write("orphan-8d.parquet", age_days=8)
    old_orphan = store.write("orphan-12d.parquet", age_days=12)

    result = tables.remove_orphan_files("raw.events", older_than_days=10)

    assert result["orphan_files"] == [old_orphan]


def test_older_than_hours_sets_the_retention_window(store) -> None:
    store.write("orphan-190h.parquet", age_days=190 / 24)
    old_orphan = store.write("orphan-210h.parquet", age_days=210 / 24)

    result = tables.remove_orphan_files("raw.events", older_than_hours=200)

    assert result["orphan_files"] == [old_orphan]


def test_lists_nested_data_files_and_reads_from_the_requested_ref(store) -> None:
    nested = store.write("day=2026-01-01/orphan.parquet", age_days=9)
    store.write("day=2026-01-01/kept.parquet", age_days=9)
    store.reference("day=2026-01-01/kept.parquet")

    result = tables.remove_orphan_files("raw.events", ref="dev")

    assert result["orphan_files"] == [nested]
    assert store.refs == ["dev"]


def test_files_without_a_modification_time_count_as_orphans(store, monkeypatch) -> None:
    fresh = store.write("fresh.parquet", age_days=0)
    listing = resource_module._list_storage_files
    monkeypatch.setattr(
        resource_module,
        "_list_storage_files",
        lambda io, location: [
            SimpleNamespace(path=info.path, mtime=None) for info in listing(io, location)
        ],
    )

    result = tables.remove_orphan_files("raw.events")

    assert result["orphan_files"] == [fresh]


def test_counts_every_orphan_but_lists_at_most_one_hundred(store) -> None:
    for index in range(101):
        store.write(f"orphan-{index:03d}.parquet", age_days=8)

    result = tables.remove_orphan_files("raw.events")

    assert result["orphan_count"] == 101
    assert len(result["orphan_files"]) == 100
    assert all(path.startswith(f"{DATA_PREFIX}/orphan-") for path in result["orphan_files"])


def test_listing_failure_reports_no_orphans(store) -> None:
    store.write("orphan-old.parquet", age_days=8)
    (store.root / DATA_PREFIX / "orphan-old.parquet").unlink()
    (store.root / DATA_PREFIX).rmdir()

    result = tables.remove_orphan_files("raw.events")

    assert result == {"orphan_count": 0, "orphan_files": [], "dry_run": True}


def test_refuses_destructive_deletion_without_touching_the_store(store) -> None:
    orphan = store.root / store.write("orphan-old.parquet", age_days=30)

    with pytest.raises(ValueError, match="Direct orphan deletion is disabled"):
        tables.remove_orphan_files("raw.events", dry_run=False)

    assert orphan.exists()
    assert store.refs == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"older_than_days": 7, "older_than_hours": 168}, "not both"),
        ({"older_than_days": 0}, "older_than_days must be positive, got 0"),
        ({"older_than_days": 6}, "7-day safety floor"),
        ({"older_than_hours": 0}, "older_than_hours must be positive, got 0"),
        ({"older_than_hours": 167}, "7-day safety floor"),
        ({"table_name": "events"}, "namespace.table format, got events"),
    ],
)
def test_rejects_invalid_arguments_before_loading_the_table(store, kwargs, message) -> None:
    table_name = kwargs.pop("table_name", "raw.events")

    with pytest.raises(ValueError, match=message):
        tables.remove_orphan_files(table_name, **kwargs)

    assert store.refs == []
