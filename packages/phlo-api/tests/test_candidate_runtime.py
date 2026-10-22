"""Owned candidates reserve before IO and reject drift before returning rows."""

import asyncio
from types import SimpleNamespace

import pytest
from phlo_api import candidate_runtime as candidates
from phlo_api.errors import ConflictError

IDENTITY = "a" * 32
BASE = "b" * 64


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    state = []

    def mutate(change):
        state[:] = change(list(state))
        return list(state)

    monkeypatch.setattr(candidates, "project_root", lambda: tmp_path)
    monkeypatch.setattr(candidates, "_records", lambda: list(state))
    monkeypatch.setattr(candidates, "_mutate", mutate)
    heads = {"main": BASE}
    calls = []

    async def nessie(method, path, **kwargs):
        if method == "POST":
            assert state[0]["state"] == "preparing"
            ref = kwargs["params"]["name"]
            assert ref not in heads
            heads[ref] = BASE
            calls.append(("fork", ref))
            return {"reference": {"name": ref, "hash": BASE}}
        ref = path.rsplit("/", 1)[1]
        return {"name": ref, "hash": heads[ref], "type": "BRANCH"}

    def create(root, identity, ref):
        calls.append(("catalog", ref))
        return {"catalog": "iceberg_candidate_" + identity, "catalog_properties_digest": "digest"}

    provider = SimpleNamespace(
        create_catalog=create,
        verify_catalog=lambda *args: None,
        connection=lambda root: {"endpoint": "http://primary:8080", "user": "phlo"},
    )
    monkeypatch.setattr(candidates, "_nessie", nessie)
    monkeypatch.setattr(candidates, "_provider", lambda: provider)
    return state, heads, calls, provider


def prepare():
    return asyncio.run(
        candidates.provision_candidate(
            env="sandbox",
            actor="owner",
            kind="upload",
            operation_id=IDENTITY,
            base_ref="main",
            base_hash=BASE,
        )
    )


def test_owned_candidate_reserves_before_io_and_replay_does_not_provision(runtime):
    state, _, calls, _ = runtime
    binding = prepare()
    assert binding["candidate_ref"] == "sandbox-upload-" + IDENTITY
    assert binding["state"] == "ready" and len(state) == 1
    assert prepare() == binding and len(calls) == 2
    assert candidates.candidate_connection(binding)["endpoint"] == "http://primary:8080"
    with pytest.raises(ConflictError):
        asyncio.run(
            candidates.provision_candidate(
                env="sandbox",
                actor="other",
                kind="upload",
                operation_id=IDENTITY,
                base_ref="main",
                base_hash=BASE,
            )
        )


def test_ambiguous_preparation_is_not_adopted_or_replayed(runtime):
    state, _, calls, provider = runtime

    def fail(*args):
        raise TimeoutError("catalog response lost")

    provider.create_catalog = fail
    with pytest.raises(TimeoutError):
        prepare()
    assert state[0]["state"] == "unknown"
    with pytest.raises(ConflictError, match="reconciliation"):
        prepare()
    assert len(calls) == 1


@pytest.mark.parametrize("changed", ["main", "candidate"])
def test_candidate_or_base_drift_is_rejected(runtime, changed):
    _, heads, _, _ = runtime
    binding = prepare()
    heads["main" if changed == "main" else binding["candidate_ref"]] = "c" * 64
    with pytest.raises(ConflictError):
        asyncio.run(candidates.verify_candidate(binding, expected_hash=BASE))


def test_binding_tampering_is_rejected_before_connection(runtime):
    binding = prepare()
    with pytest.raises(ConflictError, match="ownership"):
        candidates.candidate_connection({**binding, "candidate_ref": "main"})


def test_preview_rejects_other_catalog_or_unbounded_relation(runtime):
    binding = prepare()
    for relation, limit in [
        ("iceberg.bronze.raw", 25),
        ("bronze.raw;DROP", 25),
        ("bronze.raw", 101),
    ]:
        with pytest.raises(ConflictError):
            asyncio.run(
                candidates.preview_candidate(binding, relation, expected_hash=BASE, limit=limit)
            )


def test_preview_rejects_head_drift_during_sql_before_returning_rows(runtime, monkeypatch):
    _, heads, _, provider = runtime
    binding = prepare()
    from phlo_api import branch_schema_resolution

    async def content(*args):
        return {"type": "ICEBERG_TABLE", "snapshotId": 123, "metadataLocation": "pinned"}

    monkeypatch.setattr(branch_schema_resolution, "_content", content)
    monkeypatch.setattr(
        candidates, "_table_observation", lambda *args: {"snapshot_id": "123", "row_count": 1}
    )

    def preview(*args):
        heads[binding["candidate_ref"]] = "c" * 64
        return {
            "columns": ["id"],
            "column_types": ["bigint"],
            "rows": [{"id": 1}],
            "has_more": False,
        }

    provider.preview = preview
    with pytest.raises(ConflictError, match="changed"):
        asyncio.run(
            candidates.preview_candidate(
                binding, "bronze.raw", expected_hash=BASE, expected_snapshot_id="123"
            )
        )


def test_preview_snapshot_mismatch_stops_before_query(runtime, monkeypatch):
    _, _, _, provider = runtime
    binding = prepare()
    from phlo_api import branch_schema_resolution

    async def content(*args):
        return {"type": "ICEBERG_TABLE"}

    monkeypatch.setattr(branch_schema_resolution, "_content", content)
    monkeypatch.setattr(
        candidates,
        "_table_observation",
        lambda *args: {"snapshot_id": "9007199254740993", "row_count": 1},
    )
    provider.preview = lambda *args: pytest.fail("SQL must not run for a different snapshot")
    with pytest.raises(ConflictError, match="snapshot changed"):
        asyncio.run(
            candidates.preview_candidate(
                binding, "bronze.raw", expected_hash=BASE, expected_snapshot_id="9007199254740992"
            )
        )
