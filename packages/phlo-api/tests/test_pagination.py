"""Tests for opaque cursor pagination contracts.

Pins the cursor round-trip and the (page, next_cursor) contract of
paginate_items, plus next_cursor emission from list endpoints such as the
workflow authoring view.
"""

from __future__ import annotations


from security_test_support import authenticated_client
from phlo_api.pagination import decode_cursor, encode_cursor, paginate_items


def test_cursor_round_trip_and_page_contract() -> None:
    cursor = encode_cursor(2)

    page, next_cursor = paginate_items(["a", "b", "c", "d", "e"], limit=2, cursor=cursor)

    assert decode_cursor(cursor) == 2
    assert page == ["c", "d"]
    assert next_cursor is not None
    assert decode_cursor(next_cursor) == 4


def test_authoring_workflow_list_returns_next_cursor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    workflows = tmp_path / "workflows" / "ingestion" / "demo"
    workflows.mkdir(parents=True)
    for name in ("alpha", "beta", "gamma"):
        (workflows / f"{name}.py").write_text("# workflow\n", encoding="utf-8")

    client = authenticated_client("analyst")
    first = client.get("/api/authoring/workflows?limit=2")
    second = client.get(
        "/api/authoring/workflows",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )

    assert first.status_code == 200
    assert [item["name"] for item in first.json()["items"]] == ["alpha", "beta"]
    assert first.json()["next_cursor"] is not None
    assert [item["name"] for item in second.json()["items"]] == ["gamma"]
    assert second.json()["next_cursor"] is None
