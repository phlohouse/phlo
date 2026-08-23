"""Source connector plugin classes.

This module defines the :class:`SourceConnectorPlugin` base class for implementing
data source connectors. Source connectors enable Phlo to ingest data from external
systems like APIs, databases, file systems, and message queues.

Source connectors are responsible for:
    - Connecting to external data sources
    - Fetching data in record-by-record fashion
    - Providing schema information when available
    - Testing connectivity before ingestion

Example Implementations:
    - HTTP API connectors (REST, GraphQL)
    - Database connectors (PostgreSQL, MySQL, etc.)
    - File system connectors (CSV, JSON, Parquet)
    - Message queue connectors (Kafka, RabbitMQ)
    - Cloud storage connectors (S3, GCS, Azure Blob)

Key Methods:
    - :meth:`fetch_data`: Required. Yields records from the source
    - :meth:`get_schema`: Optional. Returns column type mapping
    - :meth:`test_connection`: Optional. Validates connectivity

See Also:
    - :class:`IngestionProviderPlugin`: For advanced ingestion customization
    - :mod:`phlo.ingestion`: Public API for ingestion operations

Example:
    ```python
    from phlo.plugins.base import SourceConnectorPlugin, PluginMetadata
    from collections.abc import Iterator
    import requests

    class JSONPlaceholderConnector(SourceConnectorPlugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                name="jsonplaceholder",
                version="1.0.0",
                description="Fetch posts from JSONPlaceholder API",
                author="Example Author",
            )

        def fetch_data(self, config: dict[str, Any]) -> Iterator[dict[str, Any]]:
            endpoint = config.get("endpoint", "posts")
            response = requests.get(f"https://jsonplaceholder.typicode.com/{endpoint}")
            response.raise_for_status()
            for item in response.json():
                yield item

        def get_schema(self, config: dict[str, Any]) -> dict[str, str]:
            return {
                "userId": "int",
                "id": "int",
                "title": "string",
                "body": "string",
            }
    ```

"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from phlo.logging import get_logger
from phlo.plugins.base.plugin import Plugin

logger = get_logger(__name__)


class SourceConnectorPlugin(Plugin, ABC):
    """Base class for source connector plugins.

    Source connectors enable ingesting data from external sources
    like APIs, databases, file systems, etc.

    Example:
        ```python
        class GitHubConnector(SourceConnectorPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="github",
                    version="1.0.0",
                    description="Fetch data from GitHub API",
                    author="Phlo Team",
                )

            def fetch_data(self, config: dict) -> Iterator[dict]:
                api_token = config["api_token"]
                repo = config["repo"]

                # Fetch data from GitHub API
                for event in fetch_github_events(api_token, repo):
                    yield event

            def get_schema(self, config: dict) -> dict:
                return {
                    "id": "string",
                    "type": "string",
                    "created_at": "timestamp",
                    "actor": "object",
                }
        ```

    """

    @abstractmethod
    def fetch_data(self, config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Fetch data from the source.

        Yield one dict per record; called by Phlo's ingestion framework to
        load data. ``config`` carries connection parameters, query/filter
        settings, pagination, and credentials.

        Example:
            ```python
            def fetch_data(self, config: dict) -> Iterator[dict]:
                api_url = config["api_url"]
                api_key = config["api_key"]

                response = requests.get(api_url, headers={"Authorization": f"Bearer {api_key}"})
                for item in response.json()["items"]:
                    yield {
                        "id": item["id"],
                        "value": item["value"],
                        "timestamp": item["created_at"],
                    }
            ```

        """

    def get_schema(self, config: dict[str, Any]) -> dict[str, str] | None:
        """Get the schema of data returned by this connector.

        Optional but recommended: the returned column-name-to-type mapping
        aids type inference and validation. Return None when the schema is
        dynamic or unknown.

        Example:
            ```python
            def get_schema(self, config: dict) -> dict:
                return {
                    "id": "string",
                    "temperature": "float",
                    "timestamp": "timestamp",
                    "location": "string",
                }
            ```

        """
        return None

    def test_connection(self, config: dict[str, Any]) -> bool:
        """Test if the source is reachable with given configuration.

        Optional but recommended for debugging; returns True when the
        source is reachable with ``config``.
        """
        try:
            iterator = iter(self.fetch_data(config))
            next(iterator)
            return True
        except StopIteration:
            return True
        except Exception:
            logger.debug("source_connectivity_check_failed")
            return False
