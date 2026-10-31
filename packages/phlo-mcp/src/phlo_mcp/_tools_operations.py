"""Register authenticated authoring and operational tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import Environment, PhloApiClient
from phlo_mcp.config import McpConfig
from phlo_mcp.tracing import CanonicalTracer, trace_tool


def _register_authoring_tools(
    _write_audit_context: Callable[[str, dict[str, Any], bool], dict[str, Any]],
    _append_audit_record: Callable[[dict[str, Any]], None],
    client: PhloApiClient,
    mcp: FastMCP,
) -> None:
    @mcp.tool()
    @trace_tool
    def create_workflow(
        domain: str,
        table: str,
        unique_key: str,
        cron: str = "0 */1 * * *",
        api_base_url: str | None = None,
        fields: list[str] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Create a workflow scaffold through phlo-api when write tools are enabled."""
        payload = client.create_workflow(
            domain=domain,
            table=table,
            unique_key=unique_key,
            cron=cron,
            api_base_url=api_base_url,
            fields=fields,
            provider=provider,
        )
        target: dict[str, Any] = {"domain": domain, "table": table}
        if provider:
            target["provider"] = provider
        audit_context = _write_audit_context("create_workflow", target, False)
        _append_audit_record(audit_context)
        return {"audit_context": audit_context, "payload": payload}

    @mcp.tool()
    @trace_tool
    def validate_workflow(workflow_path: str) -> dict[str, Any]:
        """Validate a workflow file through phlo-api."""
        payload = client.validate_workflow(workflow_path)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def validate_schema(schema_path: str) -> dict[str, Any]:
        """Validate a schema file through phlo-api."""
        payload = client.validate_schema(schema_path)
        return {"api_base_url": client.api_base_url, "payload": payload}


def _register_backfill_tool(
    _write_audit_context: Callable[[str, dict[str, Any], bool], dict[str, Any]],
    tracer: CanonicalTracer,
    _append_audit_record: Callable[[dict[str, Any]], None],
    client: PhloApiClient,
    mcp: FastMCP,
) -> None:
    @mcp.tool()
    @trace_tool
    def backfill_asset(
        asset_key_path: str,
        env: Environment,
        job_name: str,
        partition_set_name: str,
        dry_run: bool = True,
        confirmed: bool = False,
        selection: Literal["explicit", "latest", "all"] = "explicit",
        partitions: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Plan or launch an asset partition backfill in prod or staging through phlo-api.

        A live backfill needs dry_run=false and confirmed=true. An explicit
        selection needs unique partition keys; latest and all take none.
        """
        with tracer.start_as_current_span("phlo.orchestrator.asset.backfill"):
            target: dict[str, Any] = {
                "asset_key_path": asset_key_path,
                "env": env,
                "job_name": job_name,
                "partition_set_name": partition_set_name,
                "selection": selection,
            }
            if partitions:
                target["partitions"] = partitions
            if idempotency_key:
                target["idempotency_key"] = idempotency_key
            audit_context = _write_audit_context("backfill_asset", target, dry_run)
            _append_audit_record(audit_context)
            payload = client.backfill_asset(
                asset_key_path,
                env,
                job_name=job_name,
                partition_set_name=partition_set_name,
                dry_run=dry_run,
                confirmed=confirmed,
                selection=selection,
                partitions=partitions,
                idempotency_key=idempotency_key,
            )
            return {"audit_context": audit_context, "payload": payload}


def _register_write_tools(
    resolved: McpConfig,
    _write_audit_context: Callable[[str, dict[str, Any], bool], dict[str, Any]],
    _append_audit_record: Callable[[dict[str, Any]], None],
    client: PhloApiClient,
    tracer: CanonicalTracer,
    mcp: FastMCP,
) -> None:
    # Write tools are registered only when both the flag is set and an API token
    # exists: without a token every guarded call would run unauthenticated, so the
    # tools stay unregistered rather than failing (or worse, succeeding) per call.
    if resolved.enable_write_tools and resolved.api_token:
        _register_authoring_tools(_write_audit_context, _append_audit_record, client, mcp)

        @mcp.tool()
        @trace_tool
        def materialize_asset(
            asset_key_path: str,
            env: Environment,
            job_name: str,
            dry_run: bool = True,
            partition_key: str | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Materialize an asset in prod or staging through one of its jobs."""
            with tracer.start_as_current_span("phlo.orchestrator.asset.materialize"):
                target = {"asset_key_path": asset_key_path, "env": env, "job_name": job_name}
                if partition_key:
                    target["partition_key"] = partition_key
                if idempotency_key:
                    target["idempotency_key"] = idempotency_key
                audit_context = _write_audit_context("materialize_asset", target, dry_run)
                _append_audit_record(audit_context)
                payload = client.materialize_asset(
                    asset_key_path,
                    env,
                    job_name=job_name,
                    dry_run=dry_run,
                    partition_key=partition_key,
                    idempotency_key=idempotency_key,
                )
                return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        @trace_tool
        def retry_failed_run(
            run_id: str,
            env: Environment,
            dry_run: bool = True,
            confirmed: bool = False,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Retry a failed run in prod or staging from its failure.

            A live retry needs dry_run=false and confirmed=true.
            """
            with tracer.start_as_current_span("phlo.orchestrator.run.retry"):
                audit_context = _write_audit_context(
                    "retry_failed_run",
                    {
                        key: value
                        for key, value in {
                            "run_id": run_id,
                            "env": env,
                            "idempotency_key": idempotency_key,
                        }.items()
                        if value
                    },
                    dry_run,
                )
                _append_audit_record(audit_context)
                payload = client.retry_run(
                    run_id,
                    env,
                    dry_run=dry_run,
                    confirmed=confirmed,
                    idempotency_key=idempotency_key,
                )
                return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        @trace_tool
        def cancel_run(
            run_id: str,
            env: Environment,
            dry_run: bool = True,
            confirmed: bool = False,
            reason: str | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Cancel a started run in prod or staging.

            A live cancel needs dry_run=false and confirmed=true.
            """
            with tracer.start_as_current_span("phlo.orchestrator.run.cancel"):
                audit_context = _write_audit_context(
                    "cancel_run",
                    {
                        key: value
                        for key, value in {
                            "run_id": run_id,
                            "env": env,
                            "reason": reason,
                            "idempotency_key": idempotency_key,
                        }.items()
                        if value
                    },
                    dry_run,
                )
                _append_audit_record(audit_context)
                payload = client.cancel_run(
                    run_id,
                    env,
                    dry_run=dry_run,
                    confirmed=confirmed,
                    reason=reason,
                    idempotency_key=idempotency_key,
                )
                return {"audit_context": audit_context, "payload": payload}

        _register_backfill_tool(_write_audit_context, tracer, _append_audit_record, client, mcp)

        @mcp.tool()
        @trace_tool
        def list_partitions(
            asset_key_path: str,
            env: Environment,
            limit: int = 100,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            """List partition keys for an asset in prod or staging, newest first."""
            with tracer.start_as_current_span("phlo.orchestrator.asset.partitions"):
                payload = client.list_partitions(asset_key_path, env, limit=limit, cursor=cursor)
                return {
                    "api_base_url": client.api_base_url,
                    "asset_key_path": asset_key_path,
                    "partitions": payload,
                }

        @mcp.tool()
        @trace_tool
        def get_run_status(run_id: str, env: Environment) -> dict[str, Any]:
            """Get canonical run detail, including status, in prod or staging."""
            with tracer.start_as_current_span("phlo.orchestrator.run.status"):
                payload = client.get_run(run_id, env)
                return {
                    "api_base_url": client.api_base_url,
                    "run_id": run_id,
                    "payload": payload,
                }

        @mcp.tool()
        @trace_tool
        def install_plugin(package_name: str) -> dict[str, Any]:
            """Install a trusted Phlo plugin package through phlo-api."""
            audit_context = _write_audit_context(
                "install_plugin", {"package_name": package_name}, False
            )
            _append_audit_record(audit_context)
            payload = client.install_plugin(package_name)
            return {"audit_context": audit_context, "payload": payload}
