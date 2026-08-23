"""Semantic layer interfaces for downstream model providers.

Providers expose named SemanticModel entries; list_models and get_model are
the whole contract, leaving storage and SQL generation to implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticModel:
    """Semantic model definition for downstream consumers."""

    name: str
    description: str | None = None
    sql: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticLayerProvider(ABC):
    """Base class for providers exposing semantic models."""

    @abstractmethod
    def list_models(self) -> Iterable[SemanticModel]:
        """Return all semantic models exposed by this provider."""
        raise NotImplementedError

    @abstractmethod
    def get_model(self, name: str) -> SemanticModel | None:
        """Return a semantic model by name when present."""
        raise NotImplementedError
