"""Transformation operation contracts.

Sync and async transformer base classes share one result type and a minimal
logger protocol, so orchestration code drives dbt-style engines without
depending on any concrete implementation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar


@dataclass
class TransformationResult:
    """Transformation execution summary."""

    status: str
    models_built: int
    models_failed: int
    tests_passed: int
    tests_failed: int
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Logger(Protocol):
    """Minimal logging protocol for transformation operations."""

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log an informational transformation message."""

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log a transformation warning message."""

    def error(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log a transformation error message."""


ContextT = TypeVar("ContextT")


class BaseTransformer(Generic[ContextT], ABC):
    """Base contract for transformation engines."""

    def __init__(self, context: ContextT, logger: Logger):
        """Store the execution ``context`` and output ``logger``."""
        self.context = context
        self.logger = logger

    @abstractmethod
    def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> TransformationResult:
        """Run transformations for ``partition_key`` (None for a full run)
        with backend-specific ``parameters``; return the execution result."""
        ...


class AsyncTransformer(Generic[ContextT], ABC):
    """Async contract for transformation engines."""

    def __init__(self, context: ContextT, logger: Logger):
        """Store the execution ``context`` and output ``logger``."""
        self.context = context
        self.logger = logger

    @abstractmethod
    async def run_transform(
        self,
        partition_key: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> TransformationResult:
        """Asynchronously run transformations for ``partition_key`` (None
        for a full run) with backend-specific ``parameters``; return the
        execution result."""
        ...
