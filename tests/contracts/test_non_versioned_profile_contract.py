"""Smoke entry point for the non-versioned profile harness.

Integration-marked check that the harness materializes a runnable local
dbt project (dbt_project.yml, profiles.yml, DuckDB file) inside its project
directory.
"""

from __future__ import annotations

import pytest
from phlo_testing.non_versioned_profile_harness import NonVersionedProfileHarness

pytestmark = pytest.mark.integration


def test_non_versioned_profile_harness_boots_local_project(
    non_versioned_profile_harness: NonVersionedProfileHarness,
) -> None:
    """The non-versioned harness should provide a runnable local dbt project."""
    assert non_versioned_profile_harness.project_dir.exists()
    assert (non_versioned_profile_harness.project_dir / "dbt_project.yml").exists()
    assert (non_versioned_profile_harness.project_dir / "profiles.yml").exists()
    assert (
        non_versioned_profile_harness.duckdb_path.parent
        == non_versioned_profile_harness.project_dir
    )
