"""Importable targets for spawned test processes.

``multiprocessing`` spawn/forkserver children re-import the module containing
their target, so process targets must live outside pytest test modules.
"""

from __future__ import annotations

import os
import time
import asyncio
from pathlib import Path
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


def process_operation_control_app(
    dsn: str,
    namespace: str,
    project_path: str,
    requests: Any,
    results: Any,
    entered: Any,
    release: Any,
) -> None:
    """Serve a fixture API in an independent process using real controls and effects."""
    import psycopg2
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from phlo.plugins.observatory_settings import StorageUnavailableError
    from phlo_api.api.operation_controls import (
        enforce_rate_limit,
        initialize_operation_controls,
        replay_or_execute,
        replay_or_execute_async,
    )
    from phlo_api.main import _storage_error_handler
    from phlo_api.observatory_api.observatory_workflow_wizard import _workflow_apply_lock

    os.environ["PHLO_PROJECT_PATH"] = project_path
    os.environ["PHLO_API_OPERATION_CONTROLS_DB_URL"] = dsn
    os.environ["PHLO_API_OPERATION_CONTROLS_NAMESPACE"] = namespace
    os.environ["PHLO_API_RATE_LIMIT_MUTATION"] = "2"
    initialize_operation_controls()
    app = FastAPI()
    app.add_exception_handler(StorageUnavailableError, _storage_error_handler)

    def provider_effect(payload: dict[str, Any]):
        conn = psycopg2.connect(dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO proof_effects(resource) VALUES (%s) RETURNING id",
                    (payload["resource"],),
                )
                effect_id = cur.fetchone()[0]
        finally:
            conn.close()
        if payload.get("hold"):
            entered.set()
            # Killing a process in multiprocessing.Event.wait can corrupt
            # its condition semaphore and deadlock fixture teardown.
            if payload.get("crash"):
                time.sleep(20)
            elif not release.wait(20):
                raise TimeoutError("Proof synchronisation timed out")
        if payload.get("unknown"):
            raise HTTPException(504, "Provider effect committed but response lost")
        return {"effect_id": effect_id}

    @app.post("/mutate")
    def mutate(payload: dict[str, Any]):
        return replay_or_execute(
            idempotency_key=payload.get("key"),
            operation="proof-mutation",
            target=payload["target"],
            exclusion_target=payload["resource"],
            execute=lambda: provider_effect(payload),
        )

    @app.post("/mutate-async")
    async def mutate_async(payload: dict[str, Any]):
        async def execute():
            return await asyncio.to_thread(provider_effect, payload)

        return await replay_or_execute_async(
            idempotency_key=payload.get("key"),
            operation="proof-mutation",
            target=payload["target"],
            exclusion_target=payload["resource"],
            execute=execute,
        )

    @app.post("/rate")
    def rate(payload: dict[str, Any]):
        enforce_rate_limit(payload["subject"], "proof-rate")
        return {"accepted": True}

    @app.post("/workflow")
    def workflow(payload: dict[str, Any]):
        with _workflow_apply_lock(Path(project_path)):
            if payload.get("hold"):
                entered.set()
                if not release.wait(20):
                    raise TimeoutError("Proof synchronisation timed out")
            return {"accepted": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        results.put("ready")
        while (command := requests.get()) is not None:
            path, payload = command
            response = client.post(path, json=payload)
            results.put((response.status_code, response.json()))
