"""Bundled-stack full-platform contract checks.

Single end-to-end integration pass over the bundled harness: materialise sample
assets, verify API/observability/CLI surfaces plus Superset and OpenMetadata,
then shut services down in stages.
"""

from __future__ import annotations

import pytest
from phlo_testing.profile_harness import BundledStackHarness

pytestmark = pytest.mark.integration


def test_bundled_stack_exercises_all_optional_packages(
    bundled_stack_harness: BundledStackHarness,
) -> None:
    """The bundled-stack harness should validate the optional package stack end to end."""
    partition_date = bundled_stack_harness.default_partition_date()

    bundled_stack_harness.ensure_full_stack_packages()
    bundled_stack_harness.verify_default_frontends()

    bundled_stack_harness.materialize(
        "dlt_posts",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )
    bundled_stack_harness.materialize(
        "posts_mart",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )
    bundled_stack_harness.materialize(
        "publish_jsonplaceholder_marts",
        timeout=1200,
        stream_output=True,
    )

    bundled_stack_harness.verify_api_stack()
    bundled_stack_harness.verify_observability_stack()
    bundled_stack_harness.verify_metrics_cli()
    bundled_stack_harness.verify_alerting_cli()
    bundled_stack_harness.verify_lineage_cli()
    bundled_stack_harness.verify_superset()

    bundled_stack_harness.stop_services(["superset", "grafana", "alloy", "loki", "prometheus"])
    bundled_stack_harness.stop_services(["hasura", "postgrest", "pgweb"])
    bundled_stack_harness.stop_services(["phlo-api", "observatory"], native=True)
    bundled_stack_harness.stop_services(["dagster-daemon", "dagster"])

    bundled_stack_harness.verify_openmetadata()
