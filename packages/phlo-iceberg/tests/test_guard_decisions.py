"""Pin refusal precedence and fail-closed evidence before simplifying orchestration."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import phlo_iceberg.resource as resource_module
import pytest
from phlo_iceberg.resource import (
    IcebergResource,
    _retention_execute_refusal,
    _retention_limit_refusal,
    _s3_inventory_object,
    _s3_inventory_page,
    inventory_owned_s3_prefix,
)


class Pages:
    """A client that records requests and can fail after an observed page."""

    def __init__(self, pages: list[object]) -> None:
        self.pages = iter(pages)
        self.calls: list[dict[str, object]] = []

    def call_s3(self, operation: str, **request: object) -> object:
        assert operation == "list_objects_v2"
        self.calls.append(request)
        page = next(self.pages)
        if isinstance(page, Exception):
            raise page
        return page


@pytest.mark.parametrize(
    ("page", "failure"),
    [
        (None, "non-mapping page"),
        ({"Contents": None}, "non-list Contents"),
        ({"Contents": [None]}, "malformed object entry"),
        ({"Contents": [{"Key": "owned/a", "Size": "1"}]}, "without size"),
        (
            {"Contents": [{"Key": "owned/a", "Size": 1, "LastModified": "bad"}]},
            "malformed LastModified",
        ),
        ({"Contents": [{"Key": "owned/a", "Size": 1, "ETag": 3}]}, "malformed version evidence"),
        ({"IsTruncated": True, "NextContinuationToken": ""}, "before completion"),
        ({"IsTruncated": 0}, "inconsistent terminal"),
        ({"IsTruncated": False, "NextContinuationToken": "unexpected"}, "inconsistent terminal"),
        (OSError("lost connection"), "ListObjectsV2 failed"),
    ],
)
@pytest.mark.parametrize("pure", [False, True])
def test_inventory_discards_prior_pages(page: object, failure: str, pure: bool) -> None:
    if pure and not isinstance(page, Exception):
        observed = {
            "owned/first": _s3_inventory_object(
                {"Key": "owned/first", "Size": 1},
                bucket="bucket",
                prefix="owned/",
            )[1]
        }
        tokens = {"next"}
        with pytest.raises(ValueError, match=failure):
            _s3_inventory_page(
                page,
                bucket="bucket",
                prefix="owned/",
                observed=observed,
                seen_tokens=tokens,
            )
        assert list(observed) == ["owned/first"] and tokens == {"next"}
        return
    client = Pages(
        [
            {
                "IsTruncated": True,
                "NextContinuationToken": "next",
                "Contents": [{"Key": "owned/first", "Size": 1}],
            },
            page,
        ]
    )
    result = inventory_owned_s3_prefix(
        location="s3a://bucket/owned",
        retention_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
        client=client,
        page_size=1,
    )
    assert not result.complete and not result.continuation_exhausted
    assert result.objects == () and result.digest is None
    assert result.page_count == (1 if isinstance(page, Exception) else 2)
    assert failure in str(result.failure)
    assert client.calls[-1] == {
        "Bucket": "bucket",
        "Prefix": "owned/",
        "MaxKeys": 1,
        "ContinuationToken": "next",
    }


@pytest.mark.parametrize(
    ("location", "size", "failure"),
    [
        ("s3://bucket", 0, "non-root S3"),
        ("s3://bucket/owned", 0, "page_size"),
        ("s3://bucket/owned", 1001, "page_size"),
    ],
)
def test_inventory_input_refusal_precedes_client_use(
    location: str, size: int, failure: str
) -> None:
    client = Pages([])
    result = inventory_owned_s3_prefix(
        location=location,
        page_size=size,
        client=client,
        retention_cutoff=datetime.now(UTC),
    )
    assert failure in str(result.failure)
    assert result.page_count == 0 and client.calls == []


@pytest.mark.parametrize("client", [object(), SimpleNamespace(call_s3=None), None])
def test_inventory_unavailable_client_is_not_a_fallback(monkeypatch, client: object) -> None:
    factory = Mock(side_effect=OSError("client unavailable"))
    monkeypatch.setattr(resource_module, "_s3_inventory_client", factory)
    result = inventory_owned_s3_prefix(
        location="s3://bucket/owned",
        retention_cutoff=datetime.now(UTC),
        client=client,
    )
    assert "S3 inventory client unavailable" in str(result.failure)
    assert result.page_count == 0 and result.objects == () and result.digest is None
    assert factory.call_count == (1 if client is None else 0)


def retention_arguments() -> dict[str, Any]:
    """Return the smallest executable plan, with no provider dependency."""
    return {
        "operation": "expire_snapshots",
        "catalog": "iceberg",
        "plan": {
            "plan_token": "exact",
            "affected_objects": 1,
            "affected_bytes": 10,
            "table_snapshot_ref_evidence": "available",
            "before_snapshot_id": 41,
            "candidate_snapshots": [{"snapshot_id": 39}],
        },
        "expected_snapshot_id": 41,
        "confirmation_token": "exact",
        "max_affected_objects": 1,
        "max_affected_bytes": 10,
    }


@pytest.mark.parametrize(
    ("changes", "plan_changes", "code"),
    [
        ({"catalog": None, "expected_snapshot_id": None}, {}, "catalog_required"),
        (
            {"expected_snapshot_id": None, "confirmation_token": "bad"},
            {},
            "snapshot_precondition_required",
        ),
        ({"confirmation_token": "bad", "max_affected_objects": None}, {}, "plan_token_invalid"),
        ({"max_affected_objects": None, "max_affected_bytes": -1}, {}, "safety_limits_required"),
        ({"max_affected_bytes": -1}, {}, "invalid_safety_limit"),
        ({"max_affected_objects": 0}, {"affected_bytes": None}, "affected_object_limit_exceeded"),
        (
            {},
            {"affected_bytes": None, "table_snapshot_ref_evidence": "unavailable"},
            "affected_bytes_unavailable",
        ),
        (
            {"max_affected_bytes": 9},
            {"table_snapshot_ref_evidence": "unavailable"},
            "affected_byte_limit_exceeded",
        ),
        (
            {},
            {"table_snapshot_ref_evidence": "unavailable", "scan_status": "unavailable"},
            "table_snapshot_ref_evidence_unavailable",
        ),
        (
            {"expected_snapshot_id": "bad"},
            {"scan_status": "unavailable"},
            "orphan_scan_unavailable",
        ),
        (
            {"expected_snapshot_id": "bad", "operation": "cleanup_orphan_files"},
            {},
            "invalid_snapshot_precondition",
        ),
        (
            {"expected_snapshot_id": 42, "operation": "cleanup_orphan_files"},
            {},
            "concurrent_change_detected",
        ),
        (
            {"operation": "cleanup_orphan_files"},
            {"candidate_snapshots": []},
            "bounded_execution_unsupported",
        ),
        ({}, {}, "maintenance_executor_required"),
    ],
)
@pytest.mark.parametrize("pure", [False, True])
def test_retention_ordered_refusals(
    changes: dict[str, Any],
    plan_changes: dict[str, Any],
    code: str,
    pure: bool,
) -> None:
    arguments = retention_arguments()
    arguments.update(changes)
    arguments["plan"].update(plan_changes)
    if pure:
        refusal = _retention_execute_refusal(**arguments)
        if code == "maintenance_executor_required":
            assert refusal is None
        else:
            assert refusal is not None and refusal[0] == code
        return
    resource = IcebergResource(ref="main")
    forbidden = Mock(side_effect=AssertionError("executor must not be touched"))
    resource._execute_snapshot_expiry = forbidden
    result = resource._validate_retention_execute(
        **arguments,
        table_name="raw.events",
        ref="main",
        operation_id="guard-test",
        executor=None if code == "maintenance_executor_required" else Mock(),
    )
    forbidden.assert_not_called()
    assert result["failure"]["code"] == code
    assert result["executed"] is False and result["accepted"] is False
    assert result["planned"]["trino_boundary"] == "not_invoked"
    assert result["retry_safe"] is (code != "bounded_execution_unsupported")


def test_retention_zero_candidates_needs_no_executor() -> None:
    arguments = retention_arguments()
    arguments["plan"].update(affected_objects=0, affected_bytes=None, candidate_snapshots=[])
    arguments["expected_snapshot_id"] = "41"
    result = IcebergResource(ref="main")._validate_retention_execute(
        **arguments,
        table_name="raw.events",
        ref="main",
        operation_id=None,
    )
    assert result["status"] == "noop" and result["accepted"] is True
    assert result["before_revision"] == 41 and result["executed"] is False


def test_pure_inventory_progress_and_object_normalization() -> None:
    modified = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    additions, token = _s3_inventory_page(
        {
            "IsTruncated": True,
            "NextContinuationToken": "next",
            "Contents": [
                {
                    "Key": "owned/a",
                    "Size": 0,
                    "LastModified": modified,
                    "VersionId": '"version"',
                    "ETag": '"ignored"',
                },
            ],
        },
        bucket="bucket",
        prefix="owned/",
        observed={},
        seen_tokens=set(),
    )
    assert token == "next"
    assert additions["owned/a"].modified_at == modified.replace(tzinfo=UTC)
    assert additions["owned/a"].checksum_or_version == "version"
    assert _s3_inventory_page(
        {"IsTruncated": False},
        bucket="bucket",
        prefix="owned/",
        observed=additions,
        seen_tokens={"next"},
    ) == ({}, None)


@pytest.mark.parametrize("same_page", [False, True])
def test_pure_inventory_rejects_duplicates_before_continuations(same_page: bool) -> None:
    item = {"Key": "owned/a", "Size": 1}
    key, observation = _s3_inventory_object(item, bucket="bucket", prefix="owned/")
    with pytest.raises(ValueError, match="repeated an object"):
        _s3_inventory_page(
            {"Contents": [item, item] if same_page else [item]},
            bucket="bucket",
            prefix="owned/",
            observed={} if same_page else {key: observation},
            seen_tokens=set(),
        )


def test_pure_inventory_rejects_repeated_token() -> None:
    with pytest.raises(ValueError, match="repeated a continuation token"):
        _s3_inventory_page(
            {"IsTruncated": True, "NextContinuationToken": "seen"},
            bucket="bucket",
            prefix="owned/",
            observed={},
            seen_tokens={"seen"},
        )


@pytest.mark.parametrize(
    ("objects", "bytes_limit", "code"),
    [
        (None, None, "safety_limits_required"),
        (-1, 0, "invalid_safety_limit"),
        (0, 0, "affected_object_limit_exceeded"),
        (1, 9, "affected_byte_limit_exceeded"),
        (1, 10, None),
    ],
)
def test_pure_retention_limits(
    objects: int | None, bytes_limit: int | None, code: str | None
) -> None:
    refusal = _retention_limit_refusal(retention_arguments()["plan"], objects, bytes_limit)
    assert (refusal[0] if refusal else None) == code
