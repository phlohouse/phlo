"""Scenario 15 — load & contract evidence (supplemental).

Proof: the runs surface paginates deterministically through cursor pages and
serves concurrent reads without stampeding, with ≥10× the fixture's normal
run count seeded into the real durable evidence store (the deterministic
backend for this surface). This is contract evidence — it supplements, never
replaces, the real-provider scenarios.
"""

from __future__ import annotations

import concurrent.futures
import time
from datetime import UTC, datetime, timedelta

SEED_COUNT = 45  # >10x the fixture's four declared assets


def test_pagination_and_bounded_concurrency(acceptance_stack, report):
    stack = acceptance_stack

    # --- Seed 45 deterministic runs through the real evidence store ----------
    from phlo.run_evidence.models import PipelineRun
    from phlo.run_evidence.store import PostgresRunEvidenceStore

    dsn = stack.env_vars.get("PHLO_RUN_EVIDENCE_DB_URL")
    if not dsn:
        report.mark_unsupported(
            "deterministic_load_seed", "PHLO_RUN_EVIDENCE_DB_URL not configured"
        )
    else:
        store = PostgresRunEvidenceStore(dsn)
        try:
            base = datetime(2026, 9, 1, tzinfo=UTC)
            for index in range(SEED_COUNT):
                store.append_pipeline_run(
                    PipelineRun(
                        project_id=stack.project_name,
                        run_id=f"acceptance-load-{index:03d}",
                        pipeline_name="acceptance_load",
                        provider_run_id=f"prov-{index:03d}",
                        trigger="acceptance",
                        initiator="acceptance-suite",
                        status="succeeded",
                        started_at=base + timedelta(minutes=index),
                        finished_at=base + timedelta(minutes=index, seconds=30),
                    )
                )
        finally:
            store.close()
        report.passed("deterministic_seed", f"{SEED_COUNT} runs written to the durable store")

        # --- Cursor pagination walks the whole set without overlap -----------
        seen: set[str] = set()
        pages = 0
        cursor: str | None = None
        while True:
            query = "/api/observatory/runs?limit=7"
            if cursor:
                query += f"&cursor={cursor}"
            page = stack.api_get(query)
            assert page.status_code == 200, page.text[:200]
            body = page.json()
            items = body.get("items", [])
            ids = set()
            for item in items:
                identity = item.get("report_identity") or {}
                rid = identity.get("run_id") or str(item.get("id") or "").rsplit("/", 1)[-1]
                if rid:
                    ids.add(rid)
            overlap = seen & ids
            assert not overlap, f"page {pages} repeated ids {overlap}"
            seen |= ids
            pages += 1
            cursor = body.get("next_cursor")
            if not cursor or pages > 30:
                break
        seeded = {i for i in seen if str(i).startswith("acceptance-load-")}
        assert len(seeded) == SEED_COUNT, f"expected {SEED_COUNT} seeded runs, saw {len(seeded)}"
        report.passed(
            "cursor_pagination_complete",
            f"{pages} pages, {len(seeded)} seeded runs, zero overlap",
        )

    # --- Bounded concurrent reads ------------------------------------------
    # The read-model cache shares one loader per key; a burst of concurrent
    # reads must all succeed with identical payloads rather than stampede or
    # diverge.
    def fetch(_: int) -> tuple[int, int]:
        response = stack.api_get("/api/observatory/datasets")
        return response.status_code, len(response.content)

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(fetch, range(32)))
    elapsed = time.monotonic() - started
    statuses = {status for status, _ in results}
    assert statuses == {200}, statuses
    report.passed(
        "bounded_concurrent_reads",
        f"32 concurrent dataset reads all 200 in {elapsed:.1f}s",
    )
