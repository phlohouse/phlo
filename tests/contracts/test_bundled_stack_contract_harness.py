"""Smoke entry point for bundled-stack profile contract tests.

Boots the real core services through BundledStackHarness and asserts
project wiring (.phlo env, workflows) plus a live Trino endpoint.
"""

from __future__ import annotations

import urllib.request

import pytest
from phlo_testing.profile_harness import BundledStackHarness

pytestmark = pytest.mark.integration


def test_bundled_stack_harness_boots_core_services(
    bundled_stack_harness: BundledStackHarness,
) -> None:
    """The bundled-stack harness should boot the real core services and project wiring."""
    assert bundled_stack_harness.project_dir.exists()
    assert (bundled_stack_harness.project_dir / ".phlo" / ".env").exists()
    assert (bundled_stack_harness.project_dir / "workflows" / "publishing").exists()

    with urllib.request.urlopen(
        f"http://127.0.0.1:{bundled_stack_harness.ports.trino}/v1/info",
        timeout=5,
    ) as response:
        assert response.status == 200
