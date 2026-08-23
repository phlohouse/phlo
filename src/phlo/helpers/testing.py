"""Testing helpers for lightweight workflow unit tests.

FakeRuntimeContext stands in for the orchestrator runtime so workflow
functions can be unit tested without Dagster installed; logging defaults to
a null logger unless one is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FakeRuntimeContext:
    """Minimal RuntimeContext-compatible object for unit tests."""

    run_id: str | None = "test-run"
    partition_key: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    resources_value: dict[str, Any] = field(default_factory=dict)
    logger_value: Any = None

    @property
    def logger(self) -> Any:
        """Return a test logger."""
        return self.logger_value or _NullLogger()

    @property
    def resources(self) -> dict[str, Any]:
        """Return test resources."""
        return self.resources_value

    @property
    def routing(self) -> Any:
        """Allow phlo.capabilities.runtime to derive routing from tags/resources."""
        return None

    def get_resource(self, name: str) -> Any:
        """Return a named test resource."""
        return self.resources_value[name]


class _NullLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        """Discard the debug record."""

    def info(self, *args: Any, **kwargs: Any) -> None:
        """Discard the info record."""

    def warning(self, *args: Any, **kwargs: Any) -> None:
        """Discard the warning record."""

    def error(self, *args: Any, **kwargs: Any) -> None:
        """Discard the error record."""


def assert_materialize_result(result: Any, *, status: str | None = None) -> None:
    """Assert a MaterializeResult-like object has expected status."""
    if status is not None:
        assert getattr(result, "status", None) == status
    assert hasattr(result, "metadata")
