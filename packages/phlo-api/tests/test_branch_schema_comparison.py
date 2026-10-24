"""Provider metadata must match immutable Nessie content before exposing schema choices."""

from types import SimpleNamespace

import pytest

from phlo_api.branch_schema_comparison import _read_fields
from phlo_api.errors import ConflictError


def test_provider_schema_read_is_bound_to_pinned_metadata(monkeypatch):
    import phlo_iceberg.catalog

    field = SimpleNamespace(
        field_id=1,
        name="potency",
        field_type=SimpleNamespace(is_primitive=True),
        required=False,
        doc=None,
    )
    table = SimpleNamespace(
        metadata_location="s3://warehouse/pinned.json",
        schema=lambda: SimpleNamespace(fields=[field]),
    )
    calls = []

    def catalog(*, ref):
        calls.append(ref)
        return SimpleNamespace(load_table=lambda key: table)

    monkeypatch.setattr(phlo_iceberg.catalog, "get_catalog", catalog)
    fields = _read_fields(
        "prod-feature",
        "silver.qc_results",
        {"type": "ICEBERG_TABLE", "metadataLocation": "s3://warehouse/pinned.json"},
    )
    assert fields["potency"]["field_id"] == 1
    assert calls == ["prod-feature"]
    with pytest.raises(ConflictError, match="no longer matches"):
        _read_fields(
            "prod-feature",
            "silver.qc_results",
            {"type": "ICEBERG_TABLE", "metadataLocation": "s3://warehouse/other.json"},
        )


def test_schema_read_rejects_missing_pinned_metadata():
    with pytest.raises(ConflictError, match="location is unavailable"):
        _read_fields("prod-feature", "silver.qc_results", {"type": "ICEBERG_TABLE"})


def test_schema_comparison_reads_immutable_content_and_returns_only_changed_fields(monkeypatch):
    import asyncio
    from phlo_api import branch_schema_comparison as comparison

    paths = []

    async def nessie(method, path):
        assert method == "GET"
        paths.append(path)
        return {"content": {"type": "ICEBERG_TABLE", "metadataLocation": path}}

    common = {"field_id": 1, "name": "sample_id", "type": "string", "required": True, "doc": None}
    added = {"field_id": 2, "name": "potency", "type": "double", "required": False, "doc": None}

    def fields(ref, key, content):
        assert key == "silver.qc_results"
        assert content["metadataLocation"] in paths
        return {"sample_id": common, **({"potency": added} if ref == "prod-feature" else {})}

    monkeypatch.setattr(comparison, "_read_fields", fields)
    result = asyncio.run(
        comparison.read_schema_comparison(
            source="prod-feature",
            target="main",
            source_hash="p2",
            target_hash="p3",
            key="silver.qc_results",
            nessie_request=nessie,
        )
    )
    assert result == [{"name": "potency", "source": added, "target": None}]
    assert set(paths) == {
        "/api/v2/trees/prod-feature@p2/contents/silver.qc_results",
        "/api/v2/trees/main@p3/contents/silver.qc_results",
    }
