"""Live phlo-observer round trip for canonical Phlo observability events.

Set ``PHLO_OBSERVER_TEST_ENDPOINT`` to an observer base URL, for example
``http://localhost:10010`` after starting the optional observability profile.
The test remains opt-in because it persists events in a real observer.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import pytest

observe_core = pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")
pytest.importorskip("phlo_observe", reason="phlo-observe SDK not installed")

import phlo.telemetry as phlo_observe  # noqa: E402
from phlo.hooks.events import HookCorrelation, IngestionEvent  # noqa: E402
from phlo.logging import LoggingSettings, bind_context, clear_context, setup_logging  # noqa: E402


@pytest.mark.integration
def test_observer_http_round_trip() -> None:
    """Persist and query logs, metrics, operations, failures, and hook events."""
    endpoint = os.environ.get("PHLO_OBSERVER_TEST_ENDPOINT")
    if not endpoint:
        pytest.skip("set PHLO_OBSERVER_TEST_ENDPOINT to run against a live observer")

    base_url = endpoint.rstrip("/")
    run_id = f"phlo-live-{uuid4()}"
    phlo_observe.reset_for_tests()
    assert phlo_observe.configure(
        enabled=True,
        runtime_backend="sync",
        drains="http",
        service_name="phlo-observe-live-test",
    )

    try:
        with phlo_observe.bind_context(
            run_id=run_id,
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="0123456789abcdef",
        ):
            phlo_observe.metric("live.rows", 7, correlation={"run_id": run_id})
            with phlo_observe.observe("live.outer"), phlo_observe.observe("live.inner"):
                pass
            with (
                pytest.raises(RuntimeError, match="expected failure"),
                phlo_observe.observe("live.failure"),
            ):
                raise RuntimeError("expected failure")

            from phlo_observe_plugin.hooks_plugin import ObserveHookPlugin

            ObserveHookPlugin()._handle(
                IngestionEvent(
                    event_type="ingestion.end",
                    asset_key="raw.live",
                    table_name="raw.live",
                    group_name="raw",
                    status="success",
                    metrics={"rows_processed": 7},
                    correlation=HookCorrelation(run_id=run_id, asset_key="raw.live"),
                )
            )

            # Keep HTTP client's INFO records out of the router: this verifies
            # a stdlib log without feeding drain transport logs back to itself.
            setup_logging(
                LoggingSettings(
                    level="WARNING", log_format="json", service_name="phlo-observe-live-test"
                ),
                force=True,
            )
            bind_context(run_id=run_id)
            logging.getLogger("phlo.live").warning("live_observer_log")
            clear_context()

        observe_core.flush_metrics()
        assert observe_core.flush()
        query = urlencode({"run_id": run_id, "limit": 100})
        with urlopen(f"{base_url}/v1/events?{query}", timeout=10) as response:  # noqa: S310
            payload: dict[str, Any] = json.load(response)
    finally:
        phlo_observe.reset_for_tests()

    events = payload["items"]
    names = {event["event"] for event in events}
    assert {
        "application.log",
        "metric.summary",
        "live.outer",
        "live.inner",
        "live.failure",
        "ingestion.load",
    } <= names
    assert all(event["correlation"]["run_id"] == run_id for event in events)
    assert all(
        event["correlation"]["trace_id"] == "0123456789abcdef0123456789abcdef" for event in events
    )
    assert (
        next(event for event in events if event["event"] == "live.failure")["outcome"] == "failure"
    )
