"""Importable targets for spawned test processes.

``multiprocessing`` spawn/forkserver children re-import the module containing
their target, so process targets must live outside pytest test modules.
"""

from __future__ import annotations

import os
from typing import Any

from phlo_api.api.operation_controls import audit_operation, resolve_idempotency_claim


def process_audit_writer(project_path: str, barrier: Any, index: int) -> None:
    """Write one distinct record after all independent processes are ready."""
    os.environ["PHLO_PROJECT_PATH"] = project_path
    barrier.wait()
    audit_operation(
        operation="process_write",
        target=str(index),
        dry_run=False,
        auth={"subject": "test", "scopes": []},
        result={"index": index},
    )


def process_idempotency_resolution(project_path: str, barrier: Any) -> None:
    """Resolve the same durable claim from an independent process."""
    os.environ["PHLO_PROJECT_PATH"] = project_path
    barrier.wait()
    resolve_idempotency_claim(
        idempotency_key="process-resolution",
        operation="cancel_run",
        target="run-process",
        resolution="safe_to_retry",
        resolved_by="operator@example.test",
        evidence={"provider_lookup": "request was not applied"},
    )
