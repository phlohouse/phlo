"""Local test mode for running tests without Docker.

Enables `phlo test --local` by automatically swapping production resources
with mock implementations backed by DuckDB.

Example:
    >>> os.environ["PHLO_TEST_LOCAL"] = "1"
    >>> # Assets automatically use mocks

"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from phlo_testing.mock_dlt import MockDLTResource, mock_dlt_source
from phlo_testing.mock_iceberg import MockIcebergCatalog
from phlo_testing.mock_trino import MockTrinoResource


class LocalTestMode:
    """Enable local test mode for a with-block.

    Replaces production resources with mocks for fast local testing.

    Example:
        >>> with LocalTestMode() as mode:
        ...     # All resources are mocked
        ...     table_store = mode.table_store
        ...     trino = mode.trino

    """

    def __init__(
        self,
        fixture_dir: Optional[Path] = None,
        use_recorded_fixtures: bool = False,
    ) -> None:
        """Initialize local test mode."""
        self.fixture_dir = fixture_dir or Path(tempfile.gettempdir()) / "phlo_test_fixtures"
        self.fixture_dir.mkdir(exist_ok=True)

        self.use_recorded_fixtures = use_recorded_fixtures
        self._original_env: dict[str, Any] = {}
        self._fixtures: dict[str, Any] = {}

        self.table_store = MockIcebergCatalog()
        self.trino = MockTrinoResource()

    def __enter__(self) -> "LocalTestMode":
        """Enter local test mode, snapshotting the environment and setting flags."""
        # Snapshot the whole environment so nested overrides cannot leak out of
        # the with-block.
        self._original_env = os.environ.copy()
        os.environ["PHLO_TEST_LOCAL"] = "1"
        os.environ["PHLO_LOG_LEVEL"] = "DEBUG"
        if self.use_recorded_fixtures:
            self._load_fixtures()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit local test mode and restore the original environment."""
        os.environ.clear()
        os.environ.update(self._original_env)

        self.table_store.close()
        self.trino.close()

    def record_fixture(self, name: str, data: Any) -> None:
        """Record a fixture for later playback."""
        fixture_file = self.fixture_dir / f"{name}.json"

        # Normalise DataFrame-like objects first; default=str below covers any
        # remaining non-JSON-native values.
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        elif hasattr(data, "to_json"):
            data = data.to_json()

        with open(fixture_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def load_fixture(self, name: str) -> Any:
        """Load a recorded fixture.

        Raises: FileNotFoundError when the fixture does not exist.

        """
        fixture_file = self.fixture_dir / f"{name}.json"

        if not fixture_file.exists():
            raise FileNotFoundError(f"Fixture not found: {fixture_file}")

        with open(fixture_file) as f:
            return json.load(f)

    def _load_fixtures(self) -> None:
        """Load all recorded fixtures."""
        if not self.fixture_dir.exists():
            return

        for fixture_file in self.fixture_dir.glob("*.json"):
            name = fixture_file.stem
            with open(fixture_file) as f:
                self._fixtures[name] = json.load(f)

    def get_resource(self, name: str) -> Any:
        """Return the named mock resource.

        Raises: ValueError when no resource exists under that name.

        """
        resources = {
            "table_store": self.table_store,
            "trino": self.trino,
        }

        if name not in resources:
            raise ValueError(f"Unknown resource: {name}")

        return resources[name]


@contextmanager
def local_test_mode(
    fixture_dir: Optional[Path] = None,
) -> Iterator["LocalTestMode"]:
    """Run a block inside local test mode.

    Example:
        >>> with local_test_mode() as mode:
        ...     # Test with mocked resources
        ...     table = mode.table_store.create_table(...)

    """
    mode = LocalTestMode(fixture_dir=fixture_dir)

    with mode:
        yield mode


class LocalTestDecorator:
    """Mark tests that should use local mode.

    Example:
        >>> @local_test
        ... def test_my_asset():
        ...     # Runs with mocked resources
        ...     pass

    """

    def __call__(self, func: Any) -> Any:
        """Wrap func so it runs inside local test mode."""

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Run the wrapped callable inside local test mode."""
            with local_test_mode():
                return func(*args, **kwargs)

        return wrapper


# Singleton decorator instance
local_test = LocalTestDecorator()


def is_local_test_mode() -> bool:
    """Check if running in local test mode."""
    return os.environ.get("PHLO_TEST_LOCAL", "").lower() in ("1", "true")


class FixtureRecorder:
    """Record fixtures from production services for replay in local mode.

    Example:
        >>> recorder = FixtureRecorder(fixture_dir)
        >>> data = recorder.record_dlt_fetch("users", fetch_users_api)

    """

    def __init__(self, fixture_dir: Optional[Path] = None) -> None:
        """Initialize recorder."""
        self.fixture_dir = fixture_dir or Path(tempfile.gettempdir()) / "phlo_fixtures"
        self.fixture_dir.mkdir(exist_ok=True)

    def record_dlt_source(
        self,
        name: str,
        source_func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Record data from a DLT source as a JSON fixture."""
        source = source_func(*args, **kwargs)
        data = list(source)

        fixture_file = self.fixture_dir / f"{name}_dlt.json"

        with open(fixture_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

        return data

    def record_sql_query(
        self,
        name: str,
        query_func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Record SQL query results as a JSON fixture."""
        results = query_func(*args, **kwargs)
        # Accept DataFrames or any iterable of records.
        if hasattr(results, "to_dict"):
            data = results.to_dict("records")
        else:
            data = list(results)

        fixture_file = self.fixture_dir / f"{name}_sql.json"

        with open(fixture_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

        return data

    def load_dlt_fixture(self, name: str) -> MockDLTResource:
        """Load a recorded DLT fixture as a MockDLTResource."""
        fixture_file = self.fixture_dir / f"{name}_dlt.json"

        if not fixture_file.exists():
            raise FileNotFoundError(f"Fixture not found: {fixture_file}")

        with open(fixture_file) as f:
            data = json.load(f)

        return mock_dlt_source(data, resource_name=name)

    def get_fixture_dir(self) -> Path:
        """Get the fixture directory path."""
        return self.fixture_dir

    def list_fixtures(self) -> list[str]:
        """List all recorded fixture names."""
        if not self.fixture_dir.exists():
            return []

        return sorted(f.stem for f in self.fixture_dir.glob("*.*"))


# Environment variable helpers


def enable_local_test_mode() -> None:
    """Enable local test mode for current process."""
    os.environ["PHLO_TEST_LOCAL"] = "1"


def disable_local_test_mode() -> None:
    """Disable local test mode for current process."""
    os.environ.pop("PHLO_TEST_LOCAL", None)


def set_fixture_dir(path: Path) -> None:
    """Set the fixture directory path."""
    os.environ["PHLO_FIXTURE_DIR"] = str(path)


def get_fixture_dir() -> Path:
    """Get the fixture directory path."""
    env_path = os.environ.get("PHLO_FIXTURE_DIR")

    if env_path:
        return Path(env_path)

    return Path(tempfile.gettempdir()) / "phlo_fixtures"
