"""REST API source connector plugin.

This module provides the RestAPIPlugin, a generic source connector for fetching
data from REST API endpoints. It supports configurable HTTP requests with
custom headers, query parameters, timeouts, and flexible response parsing.

Features:
    - HTTP GET requests with configurable headers and query parameters
    - Customizable timeout settings
    - Flexible response parsing with dot-notation path support
    - Automatic error handling with HTTP status code checking
    - Support for both list and object response payloads
    - Optional static schema retrieval

Example:
    Using the REST API plugin::

        from phlo_core.sources.rest_api import RestAPIPlugin

        # Create the plugin
        plugin = RestAPIPlugin()

        # Configure a data fetch
        config = {
            "url": "https://api.example.com/v1/users",
            "headers": {
                "Authorization": "Bearer your-api-token",
                "Accept": "application/json"
            },
            "params": {"page": 1, "per_page": 100},
            "timeout": 30,
            "records_path": "data.users",  # Dot-notation path to records
            "schema": {
                "id": "int",
                "name": "str",
                "email": "str"
            }
        }

        # Fetch and process records
        for record in plugin.fetch_data(config):
            print(f"User: {record['name']}")

        # Get schema if available
        schema = plugin.get_schema(config)

"""

from typing import Any

import requests

from phlo.plugins import PluginMetadata, SourceConnectorPlugin


class RestAPIPlugin(SourceConnectorPlugin):
    """Generic REST API source connector for fetching data from HTTP endpoints.

    Handles request configuration, response parsing, and error handling;
    supports custom headers, query parameters, timeouts, dot-notation path
    extraction from nested JSON, and list or single-object responses.

    Example:
        Basic usage with a simple API::

            from phlo_core.sources.rest_api import RestAPIPlugin

            plugin = RestAPIPlugin()
            config = {
                "url": "https://jsonplaceholder.typicode.com/posts",
                "timeout": 10
            }

            for post in plugin.fetch_data(config):
                print(post["title"])

        Advanced usage with authentication and nested data::

            config = {
                "url": "https://api.example.com/data",
                "headers": {"Authorization": "Bearer token"},
                "params": {"date": "2024-01-01"},
                "records_path": "response.data.items",
                "timeout": 60
            }

            records = list(plugin.fetch_data(config))

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the REST API source connector."""
        return PluginMetadata(
            name="rest_api",
            version="0.1.0",
            description="Generic REST API source connector",
            author="Phlo Team",
            tags=["source", "api"],
        )

    def fetch_data(self, config: dict[str, Any]):
        """GET the configured URL and yield each record from the JSON response.

        Config keys: ``url`` (required), ``headers``, ``params``, ``timeout``
        (default 30), ``verify_tls`` (passed to requests as ``verify``), and
        ``records_path`` (dot-separated path to the records; omit when the
        response is a list or a single record object).

        Raises requests.RequestException on request failure and ValueError
        when ``records_path`` cannot be traversed or the payload shape is
        unsupported.

        Example:
            Fetch data with authentication::

                config = {
                    "url": "https://api.example.com/users",
                    "headers": {"Authorization": "Bearer token123"},
                    "timeout": 30
                }

                for user in plugin.fetch_data(config):
                    process_user(user)

            Fetch nested data from complex response::

                config = {
                    "url": "https://api.example.com/complex",
                    "records_path": "response.data.records",
                    "params": {"limit": 100}
                }

                records = list(plugin.fetch_data(config))

        """
        url = config["url"]
        headers = config.get("headers", {})
        params = config.get("params", {})
        timeout = config.get("timeout", 30)
        records_path = config.get("records_path")

        verify_tls = config.get("verify_tls", True)
        response = requests.get(
            url, headers=headers, params=params, timeout=timeout, verify=verify_tls
        )
        response.raise_for_status()

        payload = response.json()
        records = _extract_records(payload, records_path)
        for record in records:
            yield record

    def get_schema(self, config: dict[str, Any]) -> dict[str, str] | None:
        """Return the optional ``schema`` column-to-type mapping from config, or None.

        Example:
            Get schema from config::

                config = {
                    "url": "https://api.example.com/data",
                    "schema": {
                        "id": "int",
                        "name": "str",
                        "created_at": "datetime"
                    }
                }

                schema = plugin.get_schema(config)
                # Returns: {"id": "int", "name": "str", "created_at": "datetime"}

            Without schema in config::

                config = {"url": "https://api.example.com/data"}
                schema = plugin.get_schema(config)  # Returns None

        """
        return config.get("schema")


def _extract_records(payload: Any, records_path: str | None) -> list[dict[str, Any]]:
    """Normalize a JSON payload into a list of record dicts, following ``records_path``.

    A single object payload is wrapped in a list; a list is returned as-is.
    Raises ValueError when the path cannot be traversed or the final shape is
    neither list nor dict.

    Example:
        Extract from list response::

            payload = [{"id": 1}, {"id": 2}]
            records = _extract_records(payload, None)

        Extract nested records::

            payload = {"data": {"users": [{"id": 1}]}}
            records = _extract_records(payload, "data.users")

    """
    if records_path:
        current = payload
        for key in records_path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                raise ValueError(f"records_path '{records_path}' not found in payload")
        payload = current

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]

    raise ValueError("Unsupported payload shape for REST API response")
