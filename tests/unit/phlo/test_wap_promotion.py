"""Merge-failure diagnostics recorded by the durable promotion sequence."""

from __future__ import annotations

from typing import Any

from phlo.wap_promotion import promote_wap_candidate


class _DetailCatalog:
    """Provider implementing the detail-returning merge contract."""

    def __init__(self, merged: bool, detail: str | None) -> None:
        self._merged = merged
        self._detail = detail

    def get_branch_hash(self, name: str) -> str | None:
        return {"pipeline-run-1": "src-hash", "main": "tgt-hash"}.get(name)

    def merge_branch_detail(self, source: str, target: str = "main") -> tuple[bool, str | None]:
        return self._merged, self._detail


class _BoolCatalog:
    """Provider pinned to the legacy bool-only merge contract."""

    def get_branch_hash(self, name: str) -> str | None:
        return {"pipeline-run-1": "src-hash", "main": "tgt-hash"}.get(name)

    def merge_branch(self, source: str, target: str = "main") -> bool:
        return False


def _writer() -> tuple[list[dict[str, Any]], Any]:
    writes: list[dict[str, Any]] = []

    def writer(run_id: str, **fields: Any) -> bool:
        writes.append(fields)
        return True

    return writes, writer


def _advance(catalog: Any):
    writes, writer = _writer()
    advance = promote_wap_candidate(
        logical_run_id="run-1",
        branch_name="pipeline-run-1",
        strategy="branch",
        catalog=catalog,
        report_reader=lambda run_id: None,
        report_writer=writer,
    )
    return advance, writes


def test_merge_refusal_records_provider_detail() -> None:
    advance, writes = _advance(_DetailCatalog(False, "HTTP 409: Merge conflict on keys"))

    assert advance.state == "merge_failed"
    assert advance.failure_reason == "merge_branch_returned_false"
    assert advance.failure_detail == "HTTP 409: Merge conflict on keys"
    terminal = writes[-1]
    assert terminal["status"] == "promotion_failed"
    assert terminal["failure_detail"] == "HTTP 409: Merge conflict on keys"


def test_merge_refusal_without_detail_stays_truthful() -> None:
    advance, writes = _advance(_BoolCatalog())

    assert advance.state == "merge_failed"
    assert advance.failure_reason == "merge_branch_returned_false"
    assert advance.failure_detail is None
    assert writes[-1]["failure_detail"] is None
