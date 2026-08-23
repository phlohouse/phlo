"""S3 pagination evidence for orphan-maintenance inventory.

Proves that inventory_owned_s3_prefix exhausts every continuation page with a
stable digest, fails closed on missing or repeated continuations and on
traversals mutated mid-scan, and rejects objects outside the owned prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from phlo_iceberg.resource import inventory_owned_s3_prefix


class _Pages:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.requests: list[dict[str, object]] = []

    def call_s3(self, method: str, **kwargs: object) -> dict[str, object]:
        assert method == "list_objects_v2"
        self.requests.append(kwargs)
        return self.pages.pop(0)


def _object(key: str, *, version: str = "v1", size: int = 2) -> dict[str, object]:
    return {
        "Key": key,
        "Size": size,
        "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
        "ETag": version,
    }


def test_inventory_proves_continuation_exhaustion_and_stable_digest() -> None:
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    first = _Pages(
        [
            {
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "Contents": [_object("warehouse/raw/events/data/b.parquet")],
            },
            {
                "IsTruncated": False,
                "Contents": [_object("warehouse/raw/events/data/a.parquet")],
            },
        ]
    )
    second = _Pages(
        [
            {
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "Contents": [_object("warehouse/raw/events/data/b.parquet")],
            },
            {
                "IsTruncated": False,
                "Contents": [_object("warehouse/raw/events/data/a.parquet")],
            },
        ]
    )

    one = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=cutoff,
        page_size=1,
        client=first,
    )
    two = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=cutoff,
        page_size=1,
        client=second,
    )

    assert one.complete is True
    assert one.continuation_exhausted is True
    assert one.page_count == 2
    assert [item.identity for item in one.objects] == [
        "s3://lake/warehouse/raw/events/data/a.parquet",
        "s3://lake/warehouse/raw/events/data/b.parquet",
    ]
    assert one.digest == two.digest
    assert first.requests[1]["ContinuationToken"] == "page-2"


def test_inventory_fails_closed_on_missing_or_repeated_continuations() -> None:
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    missing = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=cutoff,
        client=_Pages([{"IsTruncated": True, "Contents": []}]),
    )
    repeated = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=cutoff,
        client=_Pages(
            [
                {"IsTruncated": True, "NextContinuationToken": "again", "Contents": []},
                {"IsTruncated": True, "NextContinuationToken": "again", "Contents": []},
            ]
        ),
    )

    assert missing.complete is False
    assert missing.objects == ()
    assert missing.digest is None
    assert repeated.complete is False
    assert repeated.objects == ()
    assert repeated.page_count == 2


def test_inventory_fails_closed_when_a_mutated_traversal_repeats_an_object() -> None:
    result = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=datetime(2026, 1, 2, tzinfo=UTC),
        client=_Pages(
            [
                {
                    "IsTruncated": True,
                    "NextContinuationToken": "next",
                    "Contents": [_object("warehouse/raw/events/data/a.parquet", version="old")],
                },
                {
                    "IsTruncated": False,
                    "Contents": [_object("warehouse/raw/events/data/a.parquet", version="new")],
                },
            ]
        ),
    )

    assert result.complete is False
    assert result.objects == ()
    assert "repeated an object" in str(result.failure)


def test_inventory_rejects_objects_outside_the_owned_prefix() -> None:
    result = inventory_owned_s3_prefix(
        location="s3://lake/warehouse/raw/events/data",
        retention_cutoff=datetime(2026, 1, 2, tzinfo=UTC),
        client=_Pages(
            [{"IsTruncated": False, "Contents": [_object("warehouse/raw/other/outside.parquet")]}]
        ),
    )

    assert result.complete is False
    assert result.objects == ()
    assert "outside the owned prefix" in str(result.failure)
