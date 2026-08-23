"""OpenMetadata REST API client for metadata synchronization.

Provides authenticated access to OpenMetadata for:
- Creating/updating table entities
- Publishing lineage information
- Managing quality test results
- Syncing column-level documentation

Example:
    >>> from phlo_openmetadata import OpenMetadataClient, OpenMetadataSettings
    >>> settings = OpenMetadataSettings()
    >>> client = OpenMetadataClient(
    ...     base_url=settings.openmetadata_uri(),
    ...     username=settings.openmetadata_username,
    ...     password=settings.openmetadata_password,
    ... )
    >>> client.health_check()
    True
    >>> client.create_or_update_table("bronze", table_obj)

"""

from __future__ import annotations

import base64
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from requests import exceptions as requests_exceptions
from requests.auth import HTTPBasicAuth

from phlo.logging import get_logger
from phlo.utils import compact_dict

logger = get_logger(__name__)


@dataclass(slots=True)
class OpenMetadataColumn:
    """Column in an OpenMetadata table entity.

    Field names mirror the OpenMetadata API (camelCase); ``constraint`` uses
    values such as ``PRIMARY_KEY``.

    """

    name: str
    displayName: Optional[str] = None
    description: Optional[str] = None
    dataType: str = "UNKNOWN"
    dataLength: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    tags: Optional[list[dict[str, Any]]] = None
    constraint: Optional[str] = None
    ordinalPosition: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, excluding None values."""
        return compact_dict(asdict(self))


@dataclass(slots=True)
class OpenMetadataTable:
    """Table entity in OpenMetadata."""

    name: str
    description: Optional[str] = None
    columns: Optional[list[OpenMetadataColumn]] = None
    tableType: str = "Regular"
    owner: Optional[dict[str, Any]] = None
    tags: Optional[list[dict[str, Any]]] = None
    sourceUrl: Optional[str] = None
    location: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, converting columns to dicts."""
        return compact_dict(
            {
                "name": self.name,
                "tableType": self.tableType,
                "description": self.description,
                "columns": [col.to_dict() for col in self.columns] if self.columns else None,
                "owner": self.owner,
                "tags": self.tags,
                "sourceUrl": self.sourceUrl,
                "location": self.location,
            }
        )


@dataclass(slots=True)
class OpenMetadataLineageEdge:
    """Lineage edge between two OpenMetadata entities."""

    fromEntity: str
    toEntity: str
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for API submission."""
        return compact_dict(
            {
                "fromEntity": self.fromEntity,
                "toEntity": self.toEntity,
                "description": self.description,
            }
        )


class OpenMetadataClient:
    """Client for OpenMetadata REST API.

    Provides methods for interacting with OpenMetadata entities and
    publishing metadata, lineage, and quality results.

    The client handles authentication automatically and supports connection
    pooling via requests.Session.

    Example:
        >>> client = OpenMetadataClient(
        ...     base_url="http://openmetadata:8585/api",
        ...     username="admin",
        ...     password="admin",
        ... )
        >>> client.health_check()
        True

    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        timeout: int = 30,
        service_name: str | None = None,
        service_type: str | None = None,
        database_name: str | None = None,
    ):
        """Initialize OpenMetadata client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.service_name = service_name
        self.service_type = service_type
        self.database_name = database_name
        self._ensured_services: set[str] = set()
        self._ensured_databases: set[str] = set()
        self._ensured_schemas: dict[str, str] = {}
        self._jwt_token: str | None = None

        # Create session for connection pooling
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({"Content-Type": "application/json"})

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        log_errors: bool = True,
    ) -> dict[str, Any]:
        """Make authenticated request to OpenMetadata API.

        Returns the response JSON as a dictionary. Raises:
        requests_exceptions.RequestException when the request fails;
        failures are logged unless ``log_errors`` is False.

        """
        url = urljoin(self.base_url + "/", endpoint.lstrip("/"))

        try:
            response = self.session.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=self.timeout,
            )
            # A 401 usually means a cached bearer token expired. Drop it, fall
            # back to basic auth, re-authenticate once, and retry the request.
            if response.status_code == 401:
                if self._jwt_token:
                    self._jwt_token = None
                    self.session.headers.pop("Authorization", None)
                    self.session.auth = HTTPBasicAuth(self.username, self.password)
                if self._authenticate():
                    response = self.session.request(
                        method=method,
                        url=url,
                        json=data,
                        params=params,
                        timeout=self.timeout,
                    )
            response.raise_for_status()
            return response.json() if response.text else {}

        except requests_exceptions.RequestException as exc:
            if log_errors:
                logger.error(
                    "openmetadata_request_failed",
                    method=method,
                    endpoint=endpoint,
                    error=str(exc),
                )
            raise

    @staticmethod
    def _extract_token(payload: Any) -> Optional[str]:
        """Extract a bearer token from common OpenMetadata auth responses,
        searching the payload recursively; returns None if no token is found.

        """
        if isinstance(payload, dict):
            for key in ("accessToken", "token", "jwtToken", "idToken"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
            for key in ("data", "result", "response", "auth"):
                if key in payload:
                    token = OpenMetadataClient._extract_token(payload[key])
                    if token:
                        return token
        elif isinstance(payload, list):
            for item in payload:
                token = OpenMetadataClient._extract_token(item)
                if token:
                    return token
        return None

    def _authenticate(self) -> bool:
        """Attempt to authenticate and store a bearer token for future
        requests, returning True on success and False otherwise.

        """
        if self._jwt_token:
            return False

        if not self.username or not self.password:
            return False

        # The login API shape varies across OpenMetadata versions: both endpoint
        # paths are tried with a base64-encoded password, and non-email usernames
        # are additionally attempted against the default @open-metadata.org domain.
        endpoints = ["/v1/users/login", "/v1/auth/login"]
        encoded_password = base64.b64encode(self.password.encode("utf-8")).decode("ascii")
        payloads = [{"email": self.username, "password": encoded_password}]
        if "@" not in self.username:
            payloads.append(
                {"email": f"{self.username}@open-metadata.org", "password": encoded_password}
            )

        for endpoint in endpoints:
            url = urljoin(self.base_url + "/", endpoint.lstrip("/"))
            for payload in payloads:
                try:
                    response = self.session.request(
                        method="POST",
                        url=url,
                        json=payload,
                        timeout=self.timeout,
                        auth=None,
                    )
                except requests_exceptions.RequestException as exc:
                    logger.debug("OpenMetadata auth request failed: %s", exc)
                    continue

                if not (200 <= response.status_code < 300):
                    continue

                data = {}
                if response.text:
                    try:
                        data = response.json()
                    except ValueError:
                        data = {}

                token = self._extract_token(data)
                if token:
                    self._jwt_token = token
                    self.session.headers.update({"Authorization": f"Bearer {token}"})
                    self.session.auth = None
                    return True

        return False

    def _get_optional(self, endpoint: str) -> Optional[dict[str, Any]]:
        """GET an endpoint and return None if not found.

        Raises: requests_exceptions.HTTPError for non-404 errors.

        """
        try:
            return self._request("GET", endpoint)
        except requests_exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def _get_optional_any(self, endpoints: list[str]) -> Optional[dict[str, Any]]:
        """GET the first available endpoint, returning None if all are missing."""
        for endpoint in endpoints:
            try:
                return self._request("GET", endpoint)
            except requests_exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                raise
        return None

    def _request_fallback(
        self,
        attempts: list[tuple[str, str]],
        *,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        retry_statuses: tuple[int, ...] = (404, 405),
        log_errors: bool = True,
    ) -> dict[str, Any]:
        """Try multiple request targets, falling back on specific statuses.

        Returns the response from the first successful attempt. Raises:
        requests_exceptions.HTTPError when all attempts fail.

        """
        last_exc: requests_exceptions.HTTPError | None = None
        for method, endpoint in attempts:
            try:
                return self._request(
                    method, endpoint, data=data, params=params, log_errors=log_errors
                )
            except requests_exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in retry_statuses:
                    last_exc = exc
                    continue
                raise
        if last_exc:
            raise last_exc
        return {}

    @staticmethod
    def _sanitize_name(value: str) -> str:
        """Sanitize entity names to only alphanumeric and underscore
        characters for OpenMetadata compatibility.

        """
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
        return cleaned or "phlo"

    @staticmethod
    def _build_entity_link(table_fqn: str, column: str | None = None) -> str:
        """Build an OpenMetadata entityLink string for a table, optionally
        scoped to a single column.

        """
        if column:
            return f"<#E::table::{table_fqn}::columns::{column}>"
        return f"<#E::table::{table_fqn}>"

    def health_check(self) -> bool:
        """Check if OpenMetadata is reachable and healthy, returning True
        when healthy and False otherwise.

        """
        endpoints = ["/v1/system/version", "/health"]
        for endpoint in endpoints:
            try:
                response = self.session.request(
                    "GET", urljoin(self.base_url + "/", endpoint.lstrip("/"))
                )
                if response.status_code == 200:
                    return True
            except Exception as exc:
                logger.warning(
                    "openmetadata_health_check_failed",
                    endpoint=endpoint,
                    error=str(exc),
                )
                continue
        return False

    def get_table(self, table_fqn: str) -> Optional[dict[str, Any]]:
        """Get a table entity by fully qualified name (either
        service.database.schema.table or schema.table), or None if not found.

        """
        return self._get_optional(f"/v1/tables/name/{table_fqn}")

    def get_database_service(self, name: str) -> Optional[dict[str, Any]]:
        """Get a database service by name, or None if not found."""
        return self._get_optional(f"/v1/services/databaseServices/name/{name}")

    def create_database_service(
        self,
        name: str,
        service_type: str,
        connection: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create a database service of the given type (e.g. 'Trino',
        'Snowflake') with optional connection configuration, returning the
        created service entity.

        """
        payload: dict[str, Any] = {"name": name, "serviceType": service_type}
        if connection is not None:
            payload["connection"] = connection
        return self._request("POST", "/v1/services/databaseServices", data=payload)

    def ensure_database_service(
        self,
        name: str,
        service_type: Optional[str] = None,
        connection: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Ensure a database service exists, creating it if needed; the
        service type falls back to the instance default when not given.
        Returns the existing or created service entity. Raises: ValueError
        when the service type is required but not provided.

        """
        if name in self._ensured_services:
            return {"name": name}
        existing = self.get_database_service(name)
        if existing:
            self._ensured_services.add(name)
            return existing
        resolved_type = service_type or self.service_type
        if not resolved_type:
            raise ValueError("service_type is required to create database service")
        created = self.create_database_service(name, resolved_type, connection=connection)
        self._ensured_services.add(name)
        return created

    def get_database(self, database_fqn: str) -> Optional[dict[str, Any]]:
        """Get a database by fully qualified name, or None if not found."""
        return self._get_optional(f"/v1/databases/name/{database_fqn}")

    def create_database(self, name: str, service_fqn: str) -> dict[str, Any]:
        """Create a database within a service, returning the created
        database entity.

        """
        payload = {"name": name, "service": service_fqn}
        return self._request("POST", "/v1/databases", data=payload)

    def ensure_database(self, service_name: str, database_name: str) -> dict[str, Any]:
        """Ensure a database exists within a service, creating it if needed,
        and return the existing or created database entity.

        """
        database_fqn = f"{service_name}.{database_name}"
        if database_fqn in self._ensured_databases:
            return {"name": database_name}
        existing = self.get_database(database_fqn)
        if existing:
            self._ensured_databases.add(database_fqn)
            return existing
        created = self.create_database(database_name, service_name)
        self._ensured_databases.add(database_fqn)
        return created

    def get_database_schema(self, schema_fqn: str) -> Optional[dict[str, Any]]:
        """Get a database schema by fully qualified name, or None if not found."""
        return self._get_optional(f"/v1/databaseSchemas/name/{schema_fqn}")

    def create_database_schema(self, name: str, database_fqn: str) -> dict[str, Any]:
        """Create a schema within a database, returning the created schema
        entity.

        """
        payload = {"name": name, "database": database_fqn}
        return self._request("POST", "/v1/databaseSchemas", data=payload)

    def ensure_database_schema(
        self,
        service_name: str,
        database_name: str,
        schema_name: str,
        *,
        service_type: Optional[str] = None,
        connection: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Ensure a database schema exists, creating the parent service and
        database if needed (optional service type and connection overrides
        apply there), and return the existing or created schema entity.

        """
        schema_fqn = f"{service_name}.{database_name}.{schema_name}"
        cached_id = self._ensured_schemas.get(schema_fqn)
        if cached_id:
            return {"id": cached_id, "name": schema_name}
        self.ensure_database_service(service_name, service_type=service_type, connection=connection)
        self.ensure_database(service_name, database_name)
        existing = self.get_database_schema(schema_fqn)
        if existing:
            schema_id = existing.get("id")
            if isinstance(schema_id, str) and schema_id:
                self._ensured_schemas[schema_fqn] = schema_id
            return existing
        created = self.create_database_schema(schema_name, f"{service_name}.{database_name}")
        created_id = created.get("id") if isinstance(created, dict) else None
        if isinstance(created_id, str) and created_id:
            self._ensured_schemas[schema_fqn] = created_id
        return created

    def _schema_fqn(
        self,
        schema_name: str,
        service_name: Optional[str],
        database_name: Optional[str],
    ) -> str:
        """Build a fully qualified schema name, prefixed with the service
        and database when both are provided.

        """
        if service_name and database_name:
            return f"{service_name}.{database_name}.{schema_name}"
        return schema_name

    def search_tables(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search for tables matching a query, returning at most ``limit``
        matching table entities.

        """
        result = self._request(
            "GET",
            "/v1/search/query",
            params={"q": query, "index": "table_search_index", "size": limit},
        )
        hits = result.get("hits", {}).get("hits", [])
        return [hit.get("_source", {}) for hit in hits]

    def create_or_update_table(
        self,
        schema_name: str,
        table: OpenMetadataTable,
        *,
        service_name: Optional[str] = None,
        database_name: Optional[str] = None,
        service_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update a table entity in OpenMetadata.

        The table is filed under ``schema_name``; optional service, database,
        and service type arguments override the instance defaults, and the
        parent schema is ensured to exist first. Returns the created or
        updated table entity.

        """
        resolved_service = service_name or self.service_name
        resolved_database = database_name or self.database_name
        resolved_service_type = service_type or self.service_type

        if resolved_service and resolved_database:
            self.ensure_database_schema(
                resolved_service,
                resolved_database,
                schema_name,
                service_type=resolved_service_type,
            )

        schema_fqn = self._schema_fqn(schema_name, resolved_service, resolved_database)
        payload = table.to_dict()
        payload["databaseSchema"] = schema_fqn

        # OpenMetadata expects CreateTable schema (no id) for upserts via PUT.
        return self._request("PUT", "/v1/tables", data=payload)

    def create_lineage(
        self, from_fqn: str, to_fqn: str, description: Optional[str] = None
    ) -> dict[str, Any]:
        """Create a lineage edge between two table entities, referencing
        them by id when they exist and by fully qualified name otherwise;
        ``description`` is optional. Returns the lineage creation result.

        """
        from_entity = self.get_table(from_fqn) or {}
        to_entity = self.get_table(to_fqn) or {}
        from_ref: dict[str, Any] = {"type": "table"}
        to_ref: dict[str, Any] = {"type": "table"}
        if isinstance(from_entity.get("id"), str):
            from_ref["id"] = from_entity["id"]
        else:
            from_ref["fullyQualifiedName"] = from_fqn
        if isinstance(to_entity.get("id"), str):
            to_ref["id"] = to_entity["id"]
        else:
            to_ref["fullyQualifiedName"] = to_fqn

        edge: dict[str, Any] = {"fromEntity": from_ref, "toEntity": to_ref}
        if description:
            edge["description"] = description

        payload = {
            "edge": {
                **edge,
            }
        }
        return self._request("PUT", "/v1/lineage", data=payload)

    def list_databases(self) -> list[dict[str, Any]]:
        """List databases from OpenMetadata, returning an empty list when
        the request fails.

        """
        try:
            result = self._request("GET", "/v1/databases")
            data = result.get("data", [])
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("openmetadata_list_databases_failed", error=str(exc))
            return []

    def add_owner(self, table_fqn: str, owner_name: str) -> dict[str, Any]:
        """Set the owner of a table entity, returning the updated table.
        Raises: ValueError when the table is not found.

        """
        entity = self.get_table(table_fqn)
        if not entity:
            raise ValueError(f"Table not found: {table_fqn}")

        payload = dict(entity)
        payload["owner"] = {"name": owner_name, "type": "user"}

        return self._request("PUT", "/v1/tables", data=payload)

    def create_test_definition(
        self,
        test_name: str,
        test_type: str | None = None,
        description: Optional[str] = None,
        *,
        entity_type: str | None = None,
        parameter_definition: Optional[list[dict[str, Any]]] = None,
        test_platforms: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Create a test definition in OpenMetadata, returning the created
        or already-existing definition entity. ``test_type`` (e.g. nullCheck,
        rangeCheck) applies to the legacy API schema; ``entity_type`` is
        TABLE or COLUMN.

        """
        resolved_description = description or f"Phlo test definition: {test_name}"
        # OpenMetadata renamed the test-definition API across versions. Try the
        # current schema first, then fall back to the legacy "testType" payload;
        # a 409 means the definition already exists under either schema.
        sanitized_name = self._sanitize_name(test_name)
        data_new: dict[str, Any] = {
            "name": sanitized_name,
            "displayName": test_name,
            "entityType": entity_type or "TABLE",
            "description": resolved_description,
            "testPlatforms": test_platforms or ["OpenMetadata"],
        }
        if parameter_definition is not None:
            data_new["parameterDefinition"] = parameter_definition
        data_new = compact_dict(data_new)

        data_legacy: dict[str, Any] = {
            "name": sanitized_name,
            "displayName": test_name,
            "testType": test_type,
            "description": resolved_description,
        }
        if parameter_definition is not None:
            data_legacy["parameterDefinition"] = parameter_definition
        data_legacy = compact_dict(data_legacy)

        try:
            return self._request_fallback(
                [("POST", "/v1/dataQuality/testDefinitions"), ("POST", "/v1/testDefinitions")],
                data=data_new,
            )
        except requests_exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 409:
                existing = self.get_test_definition(sanitized_name)
                return existing or {}
            if status in (400, 404):
                return self._request("POST", "/v1/testDefinitions", data=data_legacy)
            raise

    def get_test_definition(self, name: str) -> Optional[dict[str, Any]]:
        """Get a test definition by name, or None if not found."""
        sanitized_name = self._sanitize_name(name)
        return self._get_optional_any(
            [
                f"/v1/dataQuality/testDefinitions/name/{sanitized_name}",
                f"/v1/testDefinitions/name/{sanitized_name}",
            ]
        )

    def get_test_suite(self, name: str) -> Optional[dict[str, Any]]:
        """Get a test suite by name, or None if not found."""
        return self._get_optional_any([f"/v1/dataQuality/testSuites/name/{name}"])

    def create_test_suite(
        self,
        name: str,
        table_fqn: str,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a test suite associated with a table, returning the
        created suite entity. An empty name defaults to
        ``<table_fqn>.testSuite``.

        """
        suite_name = name or f"{table_fqn}.testSuite"
        data: dict[str, Any] = {
            "name": suite_name,
            "basicEntityReference": table_fqn,
            "description": description,
        }
        data = compact_dict(data)
        return self._request("POST", "/v1/dataQuality/testSuites", data=data)

    def ensure_test_suite(
        self,
        name: str,
        table_fqn: str,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ensure a test suite exists for a table, returning the existing or
        created suite entity.

        """
        suite_name = name or f"{table_fqn}.testSuite"
        existing = self.get_test_suite(suite_name)
        if existing:
            return existing
        try:
            return self.create_test_suite(suite_name, table_fqn, description=description)
        except requests_exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 409:
                existing = self.get_test_suite(suite_name)
                return existing or {"name": suite_name}
            raise

    def create_test_case(
        self,
        test_case_name: str,
        table_fqn: str,
        test_definition_name: str,
        parameters: Optional[dict[str, Any]] = None,
        description: Optional[str] = None,
        *,
        entity_link: str | None = None,
        test_suite_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a test case for a table, returning the created test case
        entity. ``parameters`` become parameter values and ``entity_link``
        overrides the derived entity link.

        """
        sanitized_case_name = self._sanitize_name(test_case_name)
        payload: dict[str, Any] = {
            "name": sanitized_case_name,
            "displayName": sanitized_case_name,
            "entityLink": entity_link or self._build_entity_link(table_fqn),
            "testDefinition": self._sanitize_name(test_definition_name),
            "description": description,
        }
        if parameters:
            payload["parameterValues"] = [
                {"name": k, "value": str(v)} for k, v in parameters.items()
            ]

        try:
            return self._request_fallback(
                [("POST", "/v1/dataQuality/testCases"), ("POST", "/v1/testCases")],
                data=payload,
            )
        except requests_exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (400, 404, 409):
                # Some OpenMetadata versions reject bare definition names; resolve
                # the server-side FQN and retry once before giving up.
                test_def = self.get_test_definition(test_definition_name)
                if isinstance(test_def, dict):
                    test_def_fqn = test_def.get("fullyQualifiedName") or test_def.get("name")
                    if isinstance(test_def_fqn, str):
                        payload["testDefinition"] = test_def_fqn
                return self._request_fallback(
                    [("POST", "/v1/dataQuality/testCases"), ("POST", "/v1/testCases")],
                    data=payload,
                )
            raise

    def publish_test_result(
        self,
        test_case_fqn: str,
        result: str,
        test_execution_date: datetime,
        result_value: Optional[str] = None,
    ) -> dict[str, Any]:
        """Publish a test execution result ('Success' or 'Failed') for a
        test case, returning the response, or an empty dict when no result
        endpoint is available.

        """
        data = {
            "result": result,
            "testCaseStatus": result,
            "timestamp": int(test_execution_date.timestamp() * 1000),
            "result_value": result_value,
        }
        # Result endpoints moved between OpenMetadata versions. A missing
        # endpoint (404/405, or a 500 carrying "Not Found") is skipped rather
        # than failing the whole sync: result publishing is best-effort.
        attempts = [
            ("PUT", f"/v1/dataQuality/testCases/{test_case_fqn}/testCaseResult"),
            ("POST", f"/v1/testCases/{test_case_fqn}/testCaseResult"),
            ("PUT", f"/v1/testCases/{test_case_fqn}/testCaseResult"),
        ]
        try:
            return self._request_fallback(attempts, data=data, log_errors=False)
        except requests_exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            body = exc.response.text if exc.response is not None else ""
            if status in (404, 405) or (status == 500 and "Not Found" in body):
                logger.info("OpenMetadata test result endpoint unavailable, skipping.")
                return {}
            raise

    def close(self) -> None:
        """Close underlying HTTP session.

        Should be called when done using the client to release connections.
        """
        self.session.close()

    @staticmethod
    def format_timestamp(dt: datetime) -> str:
        """Format a datetime as an ISO 8601 string with a Z suffix for
        OpenMetadata.

        """
        return dt.isoformat() + "Z"
