"""Nessie resources for branch management.

This module provides low-level and high-level Nessie REST clients for branch
operations. Includes retry logic, hash management, branch creation/deletion,
and merge operations.

Example:
    >>> from phlo_nessie.resource import NessieResource, BranchManagerResource
    >>> nessie = NessieResource()
    >>> branches = nessie.list_branches()
    >>> manager = BranchManagerResource(nessie)
    >>> manager.cleanup_branch("feature/old")

Classes:
    NessieResource: Low-level Nessie REST client with retry logic.
    BranchManagerResource: High-level convenience wrapper for branch operations.

    Dagster resources for Nessie branch management; re-exported through the phlo_nessie package.
    Builds on phlo.logging and phlo_nessie.settings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError

from phlo.logging import get_logger
from phlo_nessie.settings import get_settings

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SCHEDULE = [0.5, 1.0]


@dataclass
class BranchInfo:
    """Branch metadata returned by Nessie."""

    name: str
    hash: str | None
    created_at: datetime | None


class NessieResource:
    """Lightweight Nessie REST client.

    Provides low-level Nessie API operations with automatic retry logic
    for transient failures. Supports branch management operations including
    list, create, delete, and merge.

    Example:
        >>> nessie = NessieResource()
        >>> branches = nessie.list_branches()
        >>> nessie.create_branch("feature/new", from_ref="main")

    Uses exponential backoff retry on connection errors and 5xx responses.
    """

    def __init__(self, base_url: str | None = None):
        """Initialize a Nessie client.

        Falls back to configured settings when base_url is omitted.

        Example:
            >>> nessie = NessieResource()  # Uses default settings
            >>> nessie = NessieResource("http://custom:19120")  # Explicit URL

        """
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            settings = get_settings()
            self.base_url = f"http://{settings.nessie_host}:{settings.nessie_port}"
        logger.debug(
            "nessie_resource_initialized",
            base_url=self.base_url,
            explicit_base_url=base_url is not None,
        )

    def _url(self, path: str) -> str:
        """Build a fully qualified Nessie API URL."""
        return f"{self.base_url}{path}"

    @staticmethod
    def _status_code(response: requests.Response) -> int:
        """Return an HTTP status code, rejecting an incomplete response."""
        if response.status_code is None:
            raise RuntimeError("Nessie response did not include an HTTP status code")
        return response.status_code

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> requests.Response:
        """Execute an HTTP request with retry logic.

        Retries up to ``_MAX_RETRIES`` times on connection errors and 5xx
        responses, using exponential backoff defined by ``_BACKOFF_SCHEDULE``;
        kwargs are forwarded to :func:`requests.request`. Raises
        requests.exceptions.ConnectionError after all retries are exhausted
        and RequestException on non-retryable failures.
        """
        request_fn = getattr(requests, method.lower())
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = request_fn(url, **kwargs)
                if response.status_code >= 500 and attempt < _MAX_RETRIES:
                    logger.warning(
                        "nessie_resource_request_retry",
                        method=method,
                        url=url,
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                    time.sleep(_BACKOFF_SCHEDULE[attempt - 1])
                    continue
                return response
            except RequestsConnectionError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "nessie_resource_request_connection_retry",
                        method=method,
                        url=url,
                        attempt=attempt,
                        error=str(exc),
                    )
                    time.sleep(_BACKOFF_SCHEDULE[attempt - 1])
                    continue
                raise
        if last_exc is None:
            raise RuntimeError("Nessie request retries exhausted without an exception")
        raise last_exc

    def list_branches(self) -> list[BranchInfo]:
        """List all branch references from Nessie.

        Fetches branch metadata including name, hash, and creation timestamp;
        returns parsed :class:`BranchInfo` per branch reference and propagates
        HTTP or parsing errors.

        Example:
            >>> nessie = NessieResource()
            >>> branches = nessie.list_branches()
            >>> for branch in branches:
            ...     print(f"{branch.name}: {branch.hash[:8]}")

        """
        logger.info(
            "nessie_resource_list_branches_requested",
            base_url=self.base_url,
        )
        try:
            response = self._request("GET", self._url("/api/v1/trees"), timeout=10)
            response.raise_for_status()
            payload = response.json() or {}
        except Exception:
            logger.error(
                "nessie_resource_list_branches_failed",
                base_url=self.base_url,
                exc_info=True,
            )
            raise
        branches: list[BranchInfo] = []
        for ref in payload.get("references", []):
            if ref.get("type") != "BRANCH":
                continue
            created_at = None
            metadata = ref.get("metadata") or {}
            if isinstance(metadata, dict):
                created_raw = metadata.get("createdAt") or metadata.get("created_at")
                if isinstance(created_raw, str):
                    try:
                        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    except ValueError:
                        logger.warning(
                            "nessie_resource_branch_created_at_parse_failed",
                            branch_name=ref.get("name", ""),
                            created_at_raw=created_raw,
                        )
                        created_at = None
            branches.append(
                BranchInfo(name=ref.get("name", ""), hash=ref.get("hash"), created_at=created_at)
            )
        logger.info(
            "nessie_resource_list_branches_succeeded",
            base_url=self.base_url,
            branch_count=len(branches),
        )
        return branches

    def get_branch_hash(self, name: str) -> str | None:
        """Fetch the current hash for a branch, or None when it is missing.

        Example:
            >>> nessie = NessieResource()
            >>> hash = nessie.get_branch_hash("main")
            'abc123def456...'

        """
        logger.debug(
            "nessie_resource_get_branch_hash_requested",
            branch_name=name,
            base_url=self.base_url,
        )
        response = self._request("GET", self._url(f"/api/v1/trees/tree/{name}"), timeout=10)
        status_code = self._status_code(response)
        if status_code >= 400:
            logger.info(
                "nessie_resource_get_branch_hash_missing",
                branch_name=name,
                status_code=status_code,
            )
            return None
        data = response.json() or {}
        branch_hash = data.get("hash")
        logger.debug(
            "nessie_resource_get_branch_hash_succeeded",
            branch_name=name,
            hash_found=branch_hash is not None,
        )
        return branch_hash

    def delete_branch(self, name: str) -> bool:
        """Delete a branch by name.

        Example:
            >>> nessie = NessieResource()
            >>> deleted = nessie.delete_branch("feature/old")
            True

        """
        logger.info(
            "nessie_resource_delete_branch_requested",
            branch_name=name,
        )
        branch_hash = self.get_branch_hash(name)
        if not branch_hash:
            logger.info(
                "nessie_resource_delete_branch_missing_hash",
                branch_name=name,
            )
            return False
        # Nessie mutations are conditional on the branch hash read moments
        # ago: if the branch moved in between, Nessie rejects the delete with
        # a conflict instead of discarding commits we never saw.
        response = self._request(
            "DELETE",
            self._url(f"/api/v1/trees/branch/{name}"),
            params={"expectedHash": branch_hash},
            timeout=10,
        )
        status_code = self._status_code(response)
        deleted = status_code < 300
        logger.info(
            "nessie_resource_delete_branch_completed",
            branch_name=name,
            status_code=status_code,
            deleted=deleted,
        )
        return deleted

    def create_branch(self, name: str, from_ref: str = "main") -> str | None:
        """Create a new branch from an existing reference.

        Example:
            >>> nessie = NessieResource()
            >>> new_hash = nessie.create_branch("feature/new", from_ref="main")
            'abc123def456...'

        """
        logger.info(
            "nessie_resource_create_branch_requested",
            branch_name=name,
            from_ref=from_ref,
        )
        source_hash = self.get_branch_hash(from_ref)
        if not source_hash:
            logger.warning(
                "nessie_resource_create_branch_source_missing",
                branch_name=name,
                from_ref=from_ref,
            )
            return None
        response = self._request(
            "POST",
            self._url("/api/v1/trees/tree"),
            json={"name": name, "type": "BRANCH", "hash": source_hash},
            timeout=10,
        )
        status_code = self._status_code(response)
        if status_code >= 400:
            logger.warning(
                "nessie_resource_create_branch_failed",
                branch_name=name,
                from_ref=from_ref,
                status_code=status_code,
                body=response.text[:200],
            )
            return None
        new_hash = (response.json() or {}).get("hash")
        logger.info(
            "nessie_resource_create_branch_succeeded",
            branch_name=name,
            from_ref=from_ref,
            hash=new_hash,
        )
        return new_hash

    def merge_branch(self, source: str, target: str = "main") -> bool:
        """Merge source branch into target branch.

        Example:
            >>> nessie = NessieResource()
            >>> merged = nessie.merge_branch("feature/new", target="main")
            True

        """
        logger.info(
            "nessie_resource_merge_branch_requested",
            source=source,
            target=target,
        )
        source_hash = self.get_branch_hash(source)
        target_hash = self.get_branch_hash(target)
        if not source_hash or not target_hash:
            logger.warning(
                "nessie_resource_merge_branch_hash_missing",
                source=source,
                target=target,
                source_hash_found=source_hash is not None,
                target_hash_found=target_hash is not None,
            )
            return False
        response = self._request(
            "POST",
            self._url(f"/api/v2/trees/{target}@{target_hash}/history/merge"),
            json={
                "fromRefName": source,
                "fromHash": source_hash,
                "message": f"Merge {source} into {target}",
            },
            timeout=30,
        )
        status_code = self._status_code(response)
        merged = status_code < 300
        logger.info(
            "nessie_resource_merge_branch_completed",
            source=source,
            target=target,
            status_code=status_code,
            body=response.text[:200] if status_code >= 400 else None,
            merged=merged,
        )
        return merged


class BranchManagerResource:
    """Convenience wrapper for cleaning up Nessie branches.

    Provides high-level operations for managing pipeline branches,
    filtering out system branches like 'main' and 'dev'.

    Example:
        >>> manager = BranchManagerResource()
        >>> old_branches = manager.get_all_pipeline_branches()
        >>> for branch in old_branches:
        ...     manager.cleanup_branch(branch.name)

    """

    def __init__(self, nessie: NessieResource | None = None):
        """Initialize a branch manager.

        Creates a default :class:`NessieResource` when none is supplied.

        Example:
            >>> manager = BranchManagerResource()  # Uses default NessieResource
            >>> custom = BranchManagerResource(NessieResource("http://custom:19120"))

        """
        self._nessie = nessie or NessieResource()

    def get_all_pipeline_branches(self) -> list[BranchInfo]:
        """Return non-system branches used for pipelines.

        Excludes 'main' and 'dev', which are considered system branches.

        Example:
            >>> manager = BranchManagerResource()
            >>> pipeline_branches = manager.get_all_pipeline_branches()
            >>> print([b.name for b in pipeline_branches])
            ['feature/analytics', 'feature/ml-pipeline']

        """
        branches = self._nessie.list_branches()
        pipeline_branches = [branch for branch in branches if branch.name not in {"main", "dev"}]
        logger.info(
            "nessie_branch_manager_pipeline_branches_resolved",
            total_branch_count=len(branches),
            pipeline_branch_count=len(pipeline_branches),
        )
        return pipeline_branches

    def cleanup_branch(self, name: str) -> bool:
        """Delete a pipeline branch.

        Example:
            >>> manager = BranchManagerResource()
            >>> cleaned = manager.cleanup_branch("feature/old-experiment")
            True

        """
        logger.info(
            "nessie_branch_manager_cleanup_requested",
            branch_name=name,
        )
        cleaned = self._nessie.delete_branch(name)
        logger.info(
            "nessie_branch_manager_cleanup_completed",
            branch_name=name,
            cleaned=cleaned,
        )
        return cleaned
