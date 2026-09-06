"""Polaris management API client.

Wraps the Apache Polaris management REST API (catalog, principal, and grant
administration) plus a health probe. Used by the bootstrap hook, the CLI, and
the security readiness inspector. Requests authenticate with the bootstrap
principal over HTTP basic auth, matching Polaris's management API contract.
"""

from __future__ import annotations

from typing import Any

import requests

from phlo.logging import get_logger
from phlo_polaris.settings import PolarisSettings, get_settings

logger = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 15


class PolarisResource:
    """HTTP client for the Polaris management API."""

    _cached_token: str | None = None

    def __init__(self, settings: PolarisSettings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> PolarisSettings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def _auth(self) -> tuple[str, str]:
        client_id, _, client_secret = self.settings.polaris_root_credentials.partition(":")
        return client_id, client_secret

    def _token(self) -> str:
        """Fetch an OAuth2 bearer token for the bootstrap principal.

        The management API does not accept HTTP basic; principals
        authenticate against the OAuth2 token endpoint like any Iceberg REST
        client. Polaris prints one-time root credentials to its startup log
        (persistence is in-memory, so they rotate per boot).
        """
        client_id, _, client_secret = self.settings.polaris_root_credentials.partition(":")
        if self._cached_token:
            return self._cached_token
        response = requests.post(
            f"{self.settings.polaris_rest_catalog_uri()}/v1/oauth/tokens",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        self._cached_token = str(response.json()["access_token"])
        return self._cached_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = f"{self.settings.polaris_api_uri()}{path}"
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {self._token()}"},
            json=json_body,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            self._cached_token = None
            response = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token()}"},
                json=json_body,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        logger.debug(
            "polaris_api_request",
            method=method,
            path=path,
            status_code=response.status_code,
        )
        return response

    def health_check(self) -> bool:
        """Return whether the Polaris service responds with HTTP.

        The 1.7.0 production profile does not install the Quarkus health
        endpoints, so any HTTP response (including 404 on the Iceberg REST
        prefix) proves the server is up.
        """
        try:
            response = requests.get(
                f"{self.settings.polaris_api_uri()}/api/catalog",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            logger.warning("polaris_health_check_failed", exc_info=True)
            return False
        return response.status_code < 500

    def list_catalogs(self) -> list[dict[str, Any]]:
        """List catalogs registered in Polaris."""
        response = self._request("GET", "/api/management/v1/catalogs")
        response.raise_for_status()
        return list(response.json().get("catalogs", []))

    def get_catalog(self, name: str) -> dict[str, Any] | None:
        """Return one catalog by name, or None when absent."""
        response = self._request("GET", f"/api/management/v1/catalogs/{name}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return dict(response.json())

    def create_catalog(
        self,
        *,
        name: str,
        warehouse: str,
        endpoint: str = "http://minio:9000",
        endpoint_internal: str = "http://minio:9000",
    ) -> dict[str, Any]:
        """Create an internal Polaris catalog backed by the S3 warehouse.

        Payload follows the 1.7 management schema: the catalog nests under
        ``catalog`` with ``default-base-location`` properties, and the S3
        storage config carries the MinIO endpoint with STS vending enabled.
        ``endpoint`` is advertised to REST clients (host-reachable in local
        docker setups); ``endpoint_internal`` serves Polaris server-side IO.
        """
        payload = {
            "catalog": {
                "name": name,
                "type": "INTERNAL",
                "properties": {"default-base-location": warehouse},
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": [warehouse if warehouse.endswith("/") else warehouse + "/"],
                    "endpoint": endpoint,
                    "endpointInternal": endpoint_internal,
                    "region": "us-east-1",
                    "pathStyleAccess": True,
                    "stsUnavailable": False,
                },
            },
        }
        response = self._request("POST", "/api/management/v1/catalogs", json_body=payload)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Polaris catalog creation failed for {name!r}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return dict(response.json())

    def list_principals(self) -> list[dict[str, Any]]:
        """List service principals registered in Polaris."""
        response = self._request("GET", "/api/management/v1/principals")
        response.raise_for_status()
        return list(response.json().get("principals", []))

    def create_principal(self, *, name: str) -> dict[str, Any]:
        """Create a service principal and return its client credentials."""
        response = self._request(
            "POST",
            "/api/management/v1/principals",
            json_body={"principal": {"name": name}},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Polaris principal creation failed for {name!r}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return dict(response.json())

    def ensure_catalog_role(self, *, catalog: str, role: str) -> None:
        """Create a catalog role when absent (re-creation is a no-op)."""
        response = self._request(
            "POST",
            f"/api/management/v1/catalogs/{catalog}/catalog-roles",
            json_body={"catalogRole": {"name": role}},
        )
        if response.status_code not in (200, 201, 409):
            raise RuntimeError(
                f"Polaris catalog-role creation failed for {role!r}: "
                f"{response.status_code} {response.text[:200]}"
            )

    def add_catalog_grant(self, *, catalog: str, role: str, privilege: str) -> None:
        """Grant one catalog-scope privilege to a catalog role."""
        response = self._request(
            "PUT",
            f"/api/management/v1/catalogs/{catalog}/catalog-roles/{role}/grants",
            json_body={"grant": {"type": "catalog", "privilege": privilege}},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Polaris grant failed for {role!r}/{privilege}: "
                f"{response.status_code} {response.text[:200]}"
            )

    def ensure_principal_role(self, *, role: str) -> None:
        """Create a principal role when absent (re-creation is a no-op)."""
        response = self._request(
            "POST",
            "/api/management/v1/principal-roles",
            json_body={"principalRole": {"name": role}},
        )
        if response.status_code not in (200, 201, 409):
            raise RuntimeError(
                f"Polaris principal-role creation failed for {role!r}: "
                f"{response.status_code} {response.text[:200]}"
            )

    def assign_catalog_role(self, *, principal_role: str, catalog: str, catalog_role: str) -> None:
        """Attach a catalog role to a principal role (idempotent)."""
        response = self._request(
            "PUT",
            f"/api/management/v1/principal-roles/{principal_role}/catalog-roles/{catalog}",
            json_body={"catalogRole": {"name": catalog_role}},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Polaris catalog-role assignment failed for {principal_role!r}: "
                f"{response.status_code} {response.text[:200]}"
            )

    def assign_principal_role(self, *, principal: str, principal_role: str) -> None:
        """Attach a principal role to a principal (idempotent)."""
        response = self._request(
            "PUT",
            f"/api/management/v1/principals/{principal}/principal-roles",
            json_body={"principalRole": {"name": principal_role}},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Polaris principal-role assignment failed for {principal!r}: "
                f"{response.status_code} {response.text[:200]}"
            )

    def bootstrap_grants(self) -> dict[str, list[str]]:
        """Build the writer/reader role chains on the Phlo catalog.

        1.7 grants privileges to catalog roles (never directly to
        principals): principal -> principal role -> catalog role -> grants.
        Only catalog-scope privileges are valid here; table/view privileges
        belong on narrower namespace/table grants.
        """
        catalog = self.settings.polaris_catalog
        writer = self.settings.polaris_writer_client_id
        reader = self.settings.polaris_reader_client_id
        writer_privileges = [
            "CATALOG_MANAGE_CONTENT",
            "CATALOG_MANAGE_METADATA",
            "CATALOG_MANAGE_ACCESS",
            "NAMESPACE_CREATE",
            "TABLE_CREATE",
            "VIEW_CREATE",
        ]
        reader_privileges = [
            "CATALOG_READ_PROPERTIES",
        ]
        grants: dict[str, list[str]] = {writer: [], reader: []}
        for principal, privileges in ((writer, writer_privileges), (reader, reader_privileges)):
            catalog_role = f"{principal}_catalog"
            principal_role = f"{principal}_role"
            self.ensure_catalog_role(catalog=catalog, role=catalog_role)
            for privilege in privileges:
                self.add_catalog_grant(catalog=catalog, role=catalog_role, privilege=privilege)
                grants[principal].append(privilege)
            self.ensure_principal_role(role=principal_role)
            self.assign_catalog_role(
                principal_role=principal_role, catalog=catalog, catalog_role=catalog_role
            )
            self.assign_principal_role(principal=principal, principal_role=principal_role)
        return grants
