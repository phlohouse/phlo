"""Register authenticated authoring and operational tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import PhloApiClient
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
        dry_run: bool = True,
        partitions: list[str] | None = None,
        partition_range: dict[str, str] | None = None,
        partition_set_name: str | None = None,
        repository_location_name: str | None = None,
        repository_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Plan or launch an asset partition backfill through phlo-api."""
        with tracer.start_as_current_span("phlo.orchestrator.asset.backfill"):
            target: dict[str, Any] = {"asset_key_path": asset_key_path}
            if partitions:
                target["partitions"] = partitions
            if partition_range:
                target["partition_range"] = partition_range
            if partition_set_name:
                target["partition_set_name"] = partition_set_name
            if repository_location_name:
                target["repository_location_name"] = repository_location_name
            if repository_name:
                target["repository_name"] = repository_name
            if idempotency_key:
                target["idempotency_key"] = idempotency_key
            audit_context = _write_audit_context("backfill_asset", target, dry_run)
            _append_audit_record(audit_context)
            payload = client.backfill_asset(
                asset_key_path,
                dry_run=dry_run,
                partitions=partitions,
                partition_range=partition_range,
                partition_set_name=partition_set_name,
                repository_location_name=repository_location_name,
                repository_name=repository_name,
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
            dry_run: bool = True,
            partition_key: str | None = None,
            job_name: str | None = None,
            repository_location_name: str | None = None,
            repository_name: str | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Materialize an asset through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span("phlo.orchestrator.asset.materialize"):
                target = {"asset_key_path": asset_key_path}
                if partition_key:
                    target["partition_key"] = partition_key
                if job_name:
                    target["job_name"] = job_name
                if repository_location_name:
                    target["repository_location_name"] = repository_location_name
                if repository_name:
                    target["repository_name"] = repository_name
                if idempotency_key:
                    target["idempotency_key"] = idempotency_key
                audit_context = _write_audit_context("materialize_asset", target, dry_run)
                _append_audit_record(audit_context)
                payload = client.materialize_asset(
                    asset_key_path,
                    dry_run=dry_run,
                    partition_key=partition_key,
                    job_name=job_name,
                    repository_location_name=repository_location_name,
                    repository_name=repository_name,
                    idempotency_key=idempotency_key,
                )
                return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        @trace_tool
        def retry_failed_run(
            run_id: str,
            dry_run: bool = True,
            strategy: str = "FROM_FAILURE",
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Retry a failed orchestrator run through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span("phlo.orchestrator.run.retry"):
                audit_context = _write_audit_context(
                    "retry_failed_run",
                    {
                        key: value
                        for key, value in {
                            "run_id": run_id,
                            "strategy": strategy,
                            "idempotency_key": idempotency_key,
                        }.items()
                        if value
                    },
                    dry_run,
                )
                _append_audit_record(audit_context)
                payload = client.retry_run(
                    run_id,
                    dry_run=dry_run,
                    strategy=strategy,
                    idempotency_key=idempotency_key,
                )
                return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        @trace_tool
        def cancel_run(
            run_id: str, reason: str | None = None, idempotency_key: str | None = None
        ) -> dict[str, Any]:
            """Cancel an orchestrator run through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span("phlo.orchestrator.run.cancel"):
                audit_context = _write_audit_context(
                    "cancel_run",
                    {
                        key: value
                        for key, value in {
                            "run_id": run_id,
                            "reason": reason,
                            "idempotency_key": idempotency_key,
                        }.items()
                        if value
                    },
                    False,
                )
                _append_audit_record(audit_context)
                payload = client.cancel_run(run_id, reason=reason, idempotency_key=idempotency_key)
                return {"audit_context": audit_context, "payload": payload}

        _register_backfill_tool(_write_audit_context, tracer, _append_audit_record, client, mcp)

        @mcp.tool()
        @trace_tool
        def list_partitions(asset_key_path: str) -> dict[str, Any]:
            """List partition keys for an asset through phlo-api."""
            with tracer.start_as_current_span("phlo.orchestrator.asset.partitions"):
                payload = client.list_partitions(asset_key_path)
                return {
                    "api_base_url": client.api_base_url,
                    "asset_key_path": asset_key_path,
                    "partitions": payload,
                }

        @mcp.tool()
        @trace_tool
        def get_run_status(run_id: str) -> dict[str, Any]:
            """Get orchestrator run status through phlo-api for operational follow-up."""
            with tracer.start_as_current_span("phlo.orchestrator.run.status"):
                payload = client.get_run_status(run_id)
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
