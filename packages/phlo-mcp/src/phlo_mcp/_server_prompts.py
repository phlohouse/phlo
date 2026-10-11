"""Register the MCP agent prompts."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def _register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(name="phlo.debug_run")
    def debug_run(run_id: str) -> str:
        """Guide an agent through run failure debugging with Phlo tools."""
        return (
            f"Debug Phlo run {run_id}. Use get_run_status, get_run_logs, "
            "get_run_trace_spans, and render_run_trace_tree. Identify the failing span or "
            "log line, explain the likely root cause, and propose the smallest safe fix."
        )

    @mcp.prompt(name="phlo.triage_failure")
    def triage_failure(asset_key: str) -> str:
        """Guide an agent through asset failure triage."""
        return (
            f"Triage the latest failure for Phlo asset {asset_key}. Use "
            "inspect_materialization, render_materialization_trace_tree, get_run_logs, and "
            "get_trace_spans. Summarize impact, suspected cause, and remediation steps."
        )

    @mcp.prompt(name="phlo.audit_asset")
    def audit_asset(asset_key: str) -> str:
        """Guide an agent through an asset health audit."""
        return (
            f"Audit Phlo asset {asset_key}. Inspect asset metadata, schema, recent "
            "materializations, traces, logs, and downstream impact. Return risks, evidence, "
            "and prioritized fixes."
        )

    @mcp.prompt(name="phlo.plan_backfill")
    def plan_backfill(asset_key: str, partition_range: str) -> str:
        """Guide an agent through safe backfill planning."""
        return (
            f"Plan a safe backfill for Phlo asset {asset_key} over {partition_range}. Use "
            "list_partitions and backfill_asset with dry_run=true first. Call out expected "
            "partitions, caveats, and the exact live command only if the plan is safe."
        )

    @mcp.prompt(name="phlo.scaffold_workflow")
    def scaffold_workflow(domain: str, table: str) -> str:
        """Guide an agent through workflow scaffolding once authoring tools are available."""
        return (
            f"Scaffold a Phlo workflow for domain {domain} and table {table}. Prefer MCP "
            "authoring tools when available; otherwise inspect templates and provide a "
            "minimal workflow plan with validation and materialization steps."
        )
