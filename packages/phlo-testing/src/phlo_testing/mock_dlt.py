"""Mock DLT sources for testing without API calls.

Provides mock implementations of DLT sources that return predefined data,
enabling tests to run without external dependencies or network calls.

Example:
    >>> data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    >>> source = mock_dlt_source(data, resource_name="users")
    >>> for record in source:
    ...     print(record)

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

import pandas as pd


@dataclass
class MockDLTResource:
    """Mock DLT resource that yields predefined data.

    Mimics the interface of a DLT resource but returns fixed data
    instead of fetching from an API.

    Example:
        >>> resource = MockDLTResource(name="users", data=[{"id": 1}])
        >>> for record in resource:
        ...     print(record)
        {"id": 1}

    """

    name: str
    data: list[dict[str, Any]]
    _index: int = 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Restart iteration from the first record and return self as the iterator."""
        self._index = 0
        return self

    def __next__(self) -> dict[str, Any]:
        """Return the next record or raise StopIteration when records are exhausted."""
        if self._index >= len(self.data):
            raise StopIteration
        record = self.data[self._index]
        self._index += 1
        return record

    @property
    def resources(self) -> dict[str, Any]:
        """Return this resource's metadata, including its inferred column schema."""
        return {
            self.name: {
                "name": self.name,
                "type": "resource",
                "columns": self._infer_schema(),
            }
        }

    def _infer_schema(self) -> dict[str, str]:
        """Map first-record keys to inferred warehouse column types."""
        if not self.data:
            return {}

        first_record = self.data[0]
        schema = {}

        for key, value in first_record.items():
            if isinstance(value, int):
                schema[key] = "bigint"
            elif isinstance(value, float):
                schema[key] = "double"
            elif isinstance(value, bool):
                schema[key] = "boolean"
            elif isinstance(value, str):
                schema[key] = "text"
            elif isinstance(value, pd.Timestamp) or hasattr(value, "isoformat"):
                schema[key] = "timestamp"
            else:
                schema[key] = "text"

        return schema


@dataclass
class MockDLTSource:
    """Mock DLT source with multiple resources.

    Mimics the interface of a DLT source but returns fixed data
    instead of fetching from an API. Supports multiple resources.

    Example:
        >>> source = MockDLTSource()
        >>> source.add_resource("users", [{"id": 1}])
        >>> for record in source:
        ...     print(record)

    """

    resources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _current_resource: str | None = None
    _current_index: int = 0

    def add_resource(self, name: str, data: list[dict[str, Any]]) -> MockDLTResource:
        """Register a named resource's records and return its mock resource."""
        self.resources[name] = data
        return MockDLTResource(name=name, data=data)

    def get_resource(self, name: str) -> MockDLTResource:
        """Return the named resource; raise ValueError when it does not exist."""
        if name not in self.resources:
            raise ValueError(f"Resource {name} not found")

        return MockDLTResource(name=name, data=self.resources[name])

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Yield every record from every resource in insertion order."""
        for resource_name, data in self.resources.items():
            for record in data:
                yield record

    def for_each(self, func: Any) -> None:
        """Call func once per record across all resources (dlt compatibility)."""
        for record in self:
            func(record)


def mock_dlt_source(
    data: list[dict[str, Any]],
    resource_name: str = "default",
) -> MockDLTResource:
    """Create a mock DLT source with a single resource.

    Drop-in replacement for `dlt.resource()` that returns predefined data
    without making API calls.

    Example:
        >>> data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        >>> source = mock_dlt_source(data, resource_name="users")
        >>> for record in source:
        ...     print(record)
        {"id": 1, "name": "Alice"}
        {"id": 2, "name": "Bob"}

    """
    return MockDLTResource(name=resource_name, data=data)


def mock_dlt_source_multi(
    resources: dict[str, list[dict[str, Any]]],
) -> MockDLTSource:
    """Create a mock DLT source with multiple resources.

    `resources` maps each resource name to its record list.

    Example:
        >>> source = mock_dlt_source_multi({
        ...     "users": [{"id": 1, "name": "Alice"}],
        ...     "orders": [{"order_id": 1, "user_id": 1}],
        ... })
        >>> for record in source:
        ...     print(record)

    """
    mock_source = MockDLTSource()
    for name, data in resources.items():
        mock_source.add_resource(name, data)
    return mock_source


class MockDLTError(Exception):
    """Exception for simulating DLT errors."""

    pass


def mock_dlt_source_with_error(
    data: list[dict[str, Any]],
    resource_name: str = "default",
    error_after: int | None = None,
    error_message: str = "Mock DLT error",
) -> MockDLTResource:
    """Create a mock DLT source that raises an error after N records.

    Useful for testing error handling in ingestion pipelines.

    Yield `error_after` records, then raise MockDLTError carrying
    `error_message`; pass None for `error_after` to never raise.

    Example:
        >>> source = mock_dlt_source_with_error(
        ...     [{"id": 1}, {"id": 2}],
        ...     error_after=1,
        ...     error_message="API rate limit exceeded"
        ... )
        >>> records = list(source)  # Raises after 1 record

    """

    class ErrorRaisingResource(MockDLTResource):
        """Resource that raises an error after N records."""

        def __next__(self) -> dict[str, Any]:
            """Return the next record or raise MockDLTError past the configured threshold."""
            if error_after is not None and self._index >= error_after:
                raise MockDLTError(error_message)
            return super().__next__()

    return ErrorRaisingResource(name=resource_name, data=data)


def mock_dlt_pipeline(
    data: dict[str, list[dict[str, Any]]],
) -> MockDLTSource:
    """Create a mock DLT pipeline with multiple resources.

    Convenience function for creating a complete mock pipeline.

    Example:
        >>> pipeline = mock_dlt_pipeline({
        ...     "users": [{"id": 1, "name": "Alice"}],
        ...     "orders": [{"order_id": 1, "user_id": 1}],
        ... })

    """
    return mock_dlt_source_multi(data)


def create_mock_dlt_dataframe(
    resource: MockDLTResource,
) -> pd.DataFrame:
    """Convert mock DLT resource to pandas DataFrame.

    Helper for testing data transformations.

    Example:
        >>> source = mock_dlt_source([{"id": 1}, {"id": 2}])
        >>> df = create_mock_dlt_dataframe(source)

    """
    return pd.DataFrame(list(resource))
