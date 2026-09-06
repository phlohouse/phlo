"""Airbyte Configuration API client with fail-closed job-state handling.

Dagster owns scheduling; Airbyte owns connector execution and state. This
client starts one sync, polls its job, and only returns a verdict when the
pinned Airbyte release reports a known terminal state. Unknown or ambiguous
states raise :class:`AmbiguousJobStateError` so the Phlo asset fails closed
instead of guessing.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from phlo.logging import get_logger
from phlo_airbyte.settings import AirbyteSettings, get_settings

logger = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 30

KNOWN_TERMINAL_STATUSES: dict[str, str] = {
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}


class AmbiguousJobStateError(RuntimeError):
    """Raised when a sync job ends in an unknown or ambiguous state."""


class AirbyteClient:
    """HTTP client for the Airbyte Configuration API."""

    def __init__(self, settings: AirbyteSettings | None = None) -> None:
        self._settings = settings

    @property
    def settings(self) -> AirbyteSettings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        url = f"{self.settings.airbyte_api_uri()}{path}"
        response = requests.request(method, url, json=json_body, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        return payload if payload is not None else {}

    def health_check(self) -> bool:
        """Return whether the Airbyte server responds on its health endpoint."""
        try:
            response = requests.get(
                f"{self.settings.airbyte_api_uri()}/api/v1/health",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            logger.warning("airbyte_health_check_failed", exc_info=True)
            return False
        return response.status_code == 200

    def list_connections(self) -> list[dict[str, Any]]:
        """List connections in the configured workspace."""
        body: dict[str, Any] = {}
        if self.settings.airbyte_workspace_id:
            body["workspaceId"] = self.settings.airbyte_workspace_id
        payload = self._request("POST", "/api/v1/connections/list", json_body=body)
        return list(payload.get("connections", []))

    def trigger_sync(self, connection_id: str) -> dict[str, Any]:
        """Start one sync for a pre-existing Airbyte connection."""
        return dict(
            self._request(
                "POST",
                "/api/v1/jobs/run",
                json_body={"connectionId": connection_id, "jobType": "sync"},
            )
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return the current job record for one sync."""
        return dict(self._request("POST", "/api/v1/jobs/get", json_body={"id": int(job_id)}))

    @staticmethod
    def classify_status(status: str | None) -> str | None:
        """Map an Airbyte job status to a Phlo terminal verdict, else None.

        Raises AmbiguousJobStateError for any status that is neither a known
        terminal state nor a known in-progress state, so callers fail closed.
        """
        if status is None:
            raise AmbiguousJobStateError("Airbyte job returned no status")
        normalized = str(status).strip().lower()
        if normalized in KNOWN_TERMINAL_STATUSES:
            return KNOWN_TERMINAL_STATUSES[normalized]
        if normalized in {"pending", "running", "incomplete", "incomplete_retrying"}:
            return None
        raise AmbiguousJobStateError(
            f"Unknown Airbyte job status {status!r}; refusing to guess the outcome"
        )

    def run_sync(
        self,
        connection_id: str,
        *,
        poll_interval_seconds: int | None = None,
        timeout_seconds: int | None = None,
        clock: Any = None,
    ) -> dict[str, Any]:
        """Run one sync to a verified terminal state and return its evidence.

        The returned evidence carries the job id, connection id, terminal
        status, and timestamps for lineage. Unknown states and timeouts raise
        rather than reporting success.
        """
        settings = self.settings
        poll_interval = poll_interval_seconds or settings.airbyte_poll_interval_seconds
        timeout = timeout_seconds or settings.airbyte_sync_timeout_seconds
        sleep = clock.sleep if clock is not None else time.sleep

        started = time.time()
        job_payload = self.trigger_sync(connection_id)
        job = job_payload.get("job", job_payload)
        job_id = str(job.get("id"))
        logger.info("airbyte_sync_started", connection_id=connection_id, job_id=job_id)

        while True:
            record = self.get_job(job_id).get("job", {})
            verdict = self.classify_status(record.get("status"))
            if verdict is not None:
                evidence = {
                    "job_id": job_id,
                    "connection_id": connection_id,
                    "status": verdict,
                    "started_at": job.get("createdAt"),
                    "ended_at": record.get("updatedAt"),
                    "elapsed_seconds": round(time.time() - started, 2),
                    "records_synced": record.get("recordsSynced")
                    if isinstance(record, dict)
                    else None,
                }
                if verdict != "succeeded":
                    raise RuntimeError(
                        f"Airbyte sync {job_id} for connection {connection_id} "
                        f"ended with status {verdict!r}"
                    )
                return evidence
            if time.time() - started > timeout:
                raise TimeoutError(
                    f"Airbyte sync {job_id} for connection {connection_id} did not reach a "
                    f"terminal state within {timeout}s"
                )
            sleep(poll_interval)
