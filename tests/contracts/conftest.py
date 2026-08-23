"""Fixtures for profile-level contract tests.

Bundled-stack sessions boot the real stack only when explicitly enabled via
environment and force cleanup only when the session has no failures;
non-versioned profile harnesses skip cleanly when their dependencies are
missing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from phlo_testing.non_versioned_profile_harness import (
    NonVersionedProfileHarness,
    bootstrap_non_versioned_profile_harness,
)
from phlo_testing.profile_harness import (
    bootstrap_bundled_stack_harness,
    bundled_stack_contract_enabled,
)


@pytest.fixture(scope="session")
def bundled_stack_harness(request: pytest.FixtureRequest) -> Iterator:
    """Boot the real bundled stack when contract tests are explicitly enabled."""
    if not bundled_stack_contract_enabled():
        pytest.skip("Set PHLO_RUN_BUNDLED_STACK_CONTRACT=1 to run bundled-stack contract tests")

    harness = bootstrap_bundled_stack_harness(stream_output=True)
    try:
        yield harness
    finally:
        harness.cleanup(
            stream_output=True,
            force=request.session.testsfailed == 0,
        )


@pytest.fixture(scope="session")
def non_versioned_profile_harness() -> Iterator[NonVersionedProfileHarness]:
    """Create the lightweight non-versioned profile harness when its deps exist."""
    try:
        harness = bootstrap_non_versioned_profile_harness()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    try:
        yield harness
    finally:
        harness.cleanup()
