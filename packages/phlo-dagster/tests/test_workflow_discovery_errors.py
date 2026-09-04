"""Ensure workflow import failures remain visible to Dagster callers.

Discovery imports each workflow independently to report every broken module,
but an import failure must prevent an incomplete asset graph from loading.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from phlo.exceptions import PhloDiscoveryError
from phlo_dagster.framework.discovery import discover_user_workflows


@pytest.fixture
def isolated_workflow_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore temporary workflow module names after each discovery test."""
    monkeypatch.setitem(sys.modules, "workflows.healthy", object())
    monkeypatch.setitem(sys.modules, "workflows.broken", object())


@pytest.mark.parametrize(
    ("source", "exception_name", "exception_detail"),
    [
        ("def broken(:\n", "SyntaxError", "invalid syntax"),
        (
            "raise RuntimeError('import dependency unavailable')\n",
            "RuntimeError",
            "import dependency unavailable",
        ),
        (
            "def reject(_workflow):\n"
            "    raise ValueError('decorator registration rejected')\n\n"
            "@reject\n"
            "def broken():\n"
            "    pass\n",
            "ValueError",
            "decorator registration rejected",
        ),
    ],
    ids=("syntax", "import", "decorator"),
)
def test_workflow_import_failures_name_the_module_path_and_root_error(
    tmp_path: Path,
    source: str,
    exception_name: str,
    exception_detail: str,
    isolated_workflow_modules: None,
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    healthy_path = workflows / "healthy.py"
    imported_marker = tmp_path / "healthy-workflow-imported"
    healthy_path.write_text(f"from pathlib import Path\nPath({str(imported_marker)!r}).touch()\n")
    broken_path = workflows / "broken.py"
    broken_path.write_text(source)

    with pytest.raises(PhloDiscoveryError) as exc_info:
        discover_user_workflows(workflows, clear_registries=True)

    message = str(exc_info.value)
    assert "module=workflows.broken" in message
    assert f"path={broken_path}" in message
    assert exception_name in message
    assert exception_detail in message
    # Failure-oriented context: the traceback itself, not only the root error line.
    assert "Traceback (most recent call last):" in message
    assert f'File "{broken_path}"' in message
    assert imported_marker.exists()


def test_build_definitions_surfaces_workflow_discovery_errors() -> None:
    from phlo_dagster.framework import definitions

    with (
        patch.object(definitions, "get_settings", return_value=SimpleNamespace()),
        patch.object(
            definitions,
            "discover_user_workflows",
            side_effect=PhloDiscoveryError(
                "broken module=workflows.broken path=workflows/broken.py"
            ),
        ),
    ):
        with pytest.raises(PhloDiscoveryError, match="workflows.broken"):
            definitions.build_definitions(workflows_path="workflows")
