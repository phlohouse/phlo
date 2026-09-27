from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from phlo_api.branch_schema_resolution import (
    prepare_schema_resolutions,
    verify_schema_resolutions,
)
from phlo_api.errors import ConflictError, UnprocessableInputError

pytestmark = pytest.mark.anyio


class FakeType:
    is_primitive = True

    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


def field(field_id: int, name: str, type_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        field_id=field_id,
        name=name,
        field_type=FakeType(type_name),
        required=False,
        doc=None,
    )


class FakeUpdate:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def __enter__(self) -> FakeUpdate:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def delete_column(self, name: str) -> None:
        self.table.fields = [item for item in self.table.fields if item.name != name]

    def add_column(self, name: str, field_type: FakeType, **kwargs: object) -> None:
        next_id = max((item.field_id for item in self.table.fields), default=0) + 1
        self.table.fields.append(
            SimpleNamespace(
                field_id=next_id,
                name=name,
                field_type=field_type,
                required=kwargs.get("required", False),
                doc=kwargs.get("doc"),
            )
        )

    def update_column(self, name: str, **kwargs: object) -> None:
        item = next(item for item in self.table.fields if item.name == name)
        for attribute, value in kwargs.items():
            if value is not None:
                setattr(item, attribute, value)

    def make_column_optional(self, name: str) -> None:
        self.update_column(name, required=False)


class FakeTable:
    def __init__(self, fields: list[SimpleNamespace]) -> None:
        self.fields = fields

    def schema(self) -> SimpleNamespace:
        return SimpleNamespace(fields=self.fields)

    def update_schema(self, **_kwargs: object) -> FakeUpdate:
        return FakeUpdate(self)


class FakeCatalog:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def load_table(self, _key: tuple[str, ...]) -> FakeTable:
        return self.table


def decision(**overrides: object) -> SimpleNamespace:
    values: dict[str, Any] = {
        "env": "staging",
        "source_ref": "staging-source",
        "target_ref": "staging-target",
        "source_hash": "source-hash",
        "target_hash": "target-hash",
        "table_key": "sales.orders",
        "columns": {"status": "source"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def content(*, content_id: str = "table-id", snapshot: int = 7) -> dict[str, object]:
    return {
        "type": "ICEBERG_TABLE",
        "id": content_id,
        "snapshotId": snapshot,
        "metadataLocation": "s3://warehouse/metadata.json",
    }


class FakeNessie:
    def __init__(
        self,
        *,
        source_content: dict[str, object] | None = None,
        target_content: dict[str, object] | None = None,
    ) -> None:
        self.source_content = source_content or content()
        self.target_content = target_content or content()
        self.merged_content: dict[str, object] | None = None
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def __call__(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((method, path, kwargs))
        if "/contents/" in path:
            if "staging-source@" in path:
                return self.source_content
            if "@merged-hash/contents/" in path:
                return self.merged_content or {
                    **self.target_content,
                    "metadataLocation": "s3://warehouse/resolved.json",
                }
            if "staging-target@" in path:
                return self.target_content
            return {**self.target_content, "metadataLocation": "s3://warehouse/resolved.json"}
        if method == "POST":
            params = kwargs.get("params", {})
            return {
                "reference": {
                    "type": "BRANCH",
                    "name": params["name"],
                    "hash": "target-hash",
                }
            }
        if method == "GET":
            return {"reference": {"type": "BRANCH", "name": "temporary", "hash": "resolved-hash"}}
        return {}


def install_catalogs(monkeypatch: pytest.MonkeyPatch) -> None:
    tables = iter(
        (
            FakeTable([field(1, "id", "long"), field(2, "status", "string")]),
            FakeTable([field(1, "id", "long"), field(2, "status", "long")]),
        )
    )

    def get_catalog(*, ref: str) -> FakeCatalog:
        assert ref.startswith("phlo-schema-resolution-")
        return FakeCatalog(next(tables))

    monkeypatch.setattr(
        "phlo_iceberg.catalog.get_catalog", get_catalog
    )


async def prepare(
    nessie: FakeNessie, decisions: list[SimpleNamespace]
):
    return await prepare_schema_resolutions(
        env="staging",
        source_ref="staging-source",
        target_ref="staging-target",
        source_hash="source-hash",
        target_hash="target-hash",
        requested_keys=["sales.orders"],
        decisions=decisions,
        nessie_request=nessie,
    )


@pytest.mark.parametrize(
    "decisions",
    [[], [decision(source_hash="old-hash")]],
    ids=["unbound", "stale"],
)
async def test_rejects_unbound_or_stale_decisions(
    decisions: list[SimpleNamespace],
) -> None:
    error = UnprocessableInputError if not decisions else ConflictError
    with pytest.raises(error):
        await prepare(FakeNessie(), decisions)


@pytest.mark.parametrize("columns", [{}, {"status": "source", "extra": "target"}])
async def test_rejects_missing_or_extraneous_field_choices(
    monkeypatch: pytest.MonkeyPatch, columns: dict[str, str]
) -> None:
    install_catalogs(monkeypatch)
    with pytest.raises(UnprocessableInputError, match="exactly match"):
        await prepare(FakeNessie(), [decision(columns=columns)])


@pytest.mark.parametrize(
    ("source", "target"),
    [(content(content_id="one"), content(content_id="two")), (content(snapshot=7), content(snapshot=8))],
    ids=["content-id", "snapshot"],
)
async def test_rejects_mismatched_ids_or_snapshots(
    source: dict[str, object], target: dict[str, object]
) -> None:
    with pytest.raises(ConflictError):
        await prepare(
            FakeNessie(source_content=source, target_content=target),
            [decision()],
        )


async def test_generates_exact_key_merge_mode_content(monkeypatch: pytest.MonkeyPatch) -> None:
    install_catalogs(monkeypatch)
    nessie = FakeNessie()

    prepared = await prepare(nessie, [decision()])

    assert len(prepared.temporary_refs) == 2
    assert prepared.temporary_refs[-1][1] == "resolved-hash"
    assert prepared.key_merge_modes == (
        {
            "key": {"elements": ["sales", "orders"]},
            "mergeBehavior": "NORMAL",
            "expectedTargetContent": nessie.target_content,
            "resolvedContent": {
                **nessie.target_content,
                "metadataLocation": "s3://warehouse/resolved.json",
            },
        },
    )
    await prepared.cleanup(nessie)
    deletes = [call for call in nessie.calls if call[0] == "DELETE"]
    assert len(deletes) == 2
    assert all("staging-source" not in call[1] and "staging-target" not in call[1] for call in deletes)


async def test_verifies_the_resulting_ref_contains_the_resolved_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_catalogs(monkeypatch)
    nessie = FakeNessie()
    prepared = await prepare(nessie, [decision()])

    await verify_schema_resolutions(
        prepared=prepared,
        target_ref="staging-target",
        resulting_hash="merged-hash",
        nessie_request=nessie,
    )


async def test_rejects_a_result_ref_without_the_resolved_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_catalogs(monkeypatch)
    nessie = FakeNessie()
    prepared = await prepare(nessie, [decision()])
    nessie.merged_content = {
        **nessie.target_content,
        "metadataLocation": "s3://warehouse/other.json",
    }

    with pytest.raises(ConflictError, match="did not apply"):
        await verify_schema_resolutions(
            prepared=prepared,
            target_ref="staging-target",
            resulting_hash="merged-hash",
            nessie_request=nessie,
        )
