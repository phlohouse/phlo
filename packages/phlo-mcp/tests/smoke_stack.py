"""Live smoke test for phlo-mcp against phlo-api capability routes.

This script is intentionally not part of the default unit test path. It expects
real local services and exercises the MCP stdio transport against a live
``phlo-api`` instance.

Examples:
    uv run python packages/phlo-mcp/tests/smoke_stack.py --start-stack
    uv run python packages/phlo-mcp/tests/smoke_stack.py --run-id abc-123 --require-run-spans
    uv run python packages/phlo-mcp/tests/smoke_stack.py --asset-key silver/orders --env staging
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_DEFAULT_API_BASE_URL = "http://127.0.0.1:4000"
_DEFAULT_DAGSTER_URL = "http://127.0.0.1:3000/graphql"
_SMOKE_RUN_ID = "phlo-mcp-smoke-no-such-run"
_SMOKE_ASSET_KEY = "mcp_smoke_asset"
# Dagster puts every asset in its implicit asset job.
_DEFAULT_JOB_NAME = "__ASSET_JOB"
_SMOKE_ASSET_FILENAME = "mcp_smoke_asset.py"
_SMOKE_ASSET_SOURCE = '''"""Generated asset used by packages/phlo-mcp/tests/smoke_stack.py."""

from phlo.capabilities import AssetSpec, MaterializeResult, RunSpec, register_capability


def _run(runtime):
    return [MaterializeResult(metadata={"rows": 1})]


register_capability("asset",
    AssetSpec(
        key="mcp_smoke_asset",
        group="smoke",
        description="MCP smoke asset",
        run=RunSpec(fn=_run),
    )
)
'''


class SmokeFailure(RuntimeError):
    """Raised when a smoke check fails."""


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    project_root = _project_root(args, repo_root)

    if args.start_stack:
        _seed_smoke_asset(project_root)
        if not args.asset_key:
            args.asset_key = _SMOKE_ASSET_KEY
        _start_stack(project_root)

    try:
        _wait_for_json(f"{args.api_base_url}/health", timeout_seconds=args.timeout_seconds)
        _check_phlo_api(args)
        anyio.run(_check_mcp_stdio, args, repo_root)
    except SmokeFailure as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1

    print("SMOKE PASSED: phlo-mcp, phlo-api, and configured capability paths are reachable")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("PHLO_MCP_SMOKE_API_BASE_URL", _DEFAULT_API_BASE_URL),
        help="Base URL for the live phlo-api instance",
    )
    parser.add_argument(
        "--dagster-url",
        default=os.environ.get("PHLO_MCP_SMOKE_DAGSTER_URL", _DEFAULT_DAGSTER_URL),
        help="Dagster GraphQL URL reachable by phlo-api",
    )
    parser.add_argument(
        "--api-token",
        default=os.environ.get("PHLO_MCP_SMOKE_API_TOKEN"),
        help="Optional bearer token for protected phlo-api instances",
    )
    parser.add_argument(
        "--enable-write-tools",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_ENABLE_WRITE_TOOLS"),
        help="Enable guarded MCP write-tool registration during the smoke",
    )
    parser.add_argument(
        "--exercise-write-tools",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_EXERCISE_WRITE_TOOLS"),
        help="Call guarded write tools in dry-run mode; requires live phlo-api write endpoints",
    )
    parser.add_argument(
        "--exercise-live-write-tools",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_EXERCISE_LIVE_WRITE_TOOLS"),
        help="Call guarded write tools with dry_run=false against the selected smoke asset",
    )
    parser.add_argument(
        "--env",
        default=os.environ.get("PHLO_MCP_SMOKE_ENV", "prod"),
        help="Environment ID for environment-scoped /api/v1 reads and MCP tools",
    )
    parser.add_argument(
        "--job-name",
        default=os.environ.get("PHLO_MCP_SMOKE_JOB_NAME", _DEFAULT_JOB_NAME),
        help="Job of --asset-key that the materialize_asset write-tool smoke launches",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("PHLO_MCP_SMOKE_RUN_ID", _SMOKE_RUN_ID),
        help="Run id to query through the MCP trace-span tool",
    )
    parser.add_argument(
        "--asset-key",
        default=os.environ.get("PHLO_MCP_SMOKE_ASSET_KEY"),
        help="Optional slash-delimited asset key to query for materialization history",
    )
    parser.add_argument(
        "--require-run-spans",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_REQUIRE_RUN_SPANS"),
        help="Fail unless --run-id returns at least one trace span",
    )
    parser.add_argument(
        "--require-materialization",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_REQUIRE_MATERIALIZATION"),
        help="Fail unless --asset-key returns at least one materialization event",
    )
    parser.add_argument(
        "--start-stack",
        action="store_true",
        default=_truthy_env("PHLO_MCP_SMOKE_START_STACK"),
        help="Run phlo service init/start before checking endpoints",
    )
    parser.add_argument(
        "--project-root",
        default=os.environ.get("PHLO_MCP_SMOKE_PROJECT_ROOT"),
        help=(
            "Project directory used with --start-stack; defaults to "
            ".phlo/mcp-smoke-project under the repo"
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("PHLO_MCP_SMOKE_TIMEOUT_SECONDS", "180")),
        help="How long to wait for live endpoints",
    )
    return parser.parse_args(argv)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _project_root(args: argparse.Namespace, repo_root: Path) -> Path:
    if args.project_root:
        return Path(args.project_root).expanduser().resolve()
    return repo_root / ".phlo" / "mcp-smoke-project"


def _seed_smoke_asset(project_root: Path) -> None:
    workflows_dir = project_root / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    asset_path = workflows_dir / _SMOKE_ASSET_FILENAME
    asset_path.write_text(_SMOKE_ASSET_SOURCE, encoding="utf-8")


def _start_stack(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    phlo_dir = project_root / ".phlo"
    compose_file = phlo_dir / "docker-compose.yml"
    env = _smoke_stack_env()
    if not compose_file.exists():
        _run(
            [
                "phlo",
                "services",
                "init",
                "--profile",
                "orchestration",
                "--profile",
                "api",
                "--profile",
                "observability",
                "--force",
            ],
            project_root,
            env=env,
        )
    _run(
        [
            "phlo",
            "services",
            "start",
            "--profile",
            "orchestration",
            "--profile",
            "api",
            "--profile",
            "observability",
            "--native",
        ],
        project_root,
        env=env,
    )


def _smoke_stack_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("MINIO_API_PORT", "19000")
    env.setdefault("MINIO_CONSOLE_PORT", "19001")
    env.setdefault("CLICKSTACK_NATIVE_PORT", "19002")
    return env


def _run(command: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=600,
        env=env,
    )
    if result.returncode != 0:
        raise SmokeFailure(
            f"{' '.join(command)} failed with exit {result.returncode}\n{result.stderr}"
        )


def _wait_for_json(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise SmokeFailure(f"{url} did not become ready: {last_error}")


def _api_headers(args: argparse.Namespace) -> dict[str, str]:
    if args.api_token:
        return {"Authorization": f"Bearer {args.api_token}"}
    return {}


def _get_json(
    args: argparse.Namespace,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    response = httpx.get(url, params=params, headers=_api_headers(args), timeout=20)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise SmokeFailure(f"{url} returned error: {payload['error']}")
    return payload


def _check_phlo_api(args: argparse.Namespace) -> None:
    health = _get_json(args, f"{args.api_base_url}/api/observability/health")
    if not isinstance(health, dict) or "overall_status" not in health:
        raise SmokeFailure("phlo-api observability health response was malformed")

    runs = _get_json(args, f"{args.api_base_url}/api/v1/runs", params={"env": args.env})
    if not isinstance(runs, dict) or not isinstance(runs.get("items"), list):
        raise SmokeFailure(f"phlo-api v1 runs response was malformed: {runs}")

    filtered_spans = _get_json(
        args,
        f"{args.api_base_url}/api/observability/traces",
        params={"run_id": args.run_id, "limit": "25"},
    )
    if not isinstance(filtered_spans, list):
        raise SmokeFailure(f"filtered trace endpoint returned unexpected payload: {filtered_spans}")


async def _check_mcp_stdio(args: argparse.Namespace, repo_root: Path) -> None:
    command_args = [
        "run",
        "--package",
        "phlo-mcp",
        "phlo-mcp",
        "--api-base-url",
        args.api_base_url,
    ]
    if args.api_token:
        command_args.extend(["--api-token", args.api_token])
    if args.enable_write_tools:
        command_args.append("--enable-write-tools")

    params = StdioServerParameters(command="uv", args=command_args, cwd=repo_root)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {
                "get_platform_health",
                "get_dashboard_links",
                "get_run_trace_spans",
                "get_trace_spans",
                "render_trace_spans_tree",
                "get_materialization_history",
            }
            missing = sorted(expected - names)
            if missing:
                raise SmokeFailure(f"MCP server is missing expected tools: {missing}")
            write_tools = {"materialize_asset", "retry_failed_run", "get_run_status"}
            registered_write_tools = names & write_tools
            if args.enable_write_tools and args.api_token:
                missing_write_tools = sorted(write_tools - names)
                if missing_write_tools:
                    raise SmokeFailure(
                        f"MCP server is missing enabled write tools: {missing_write_tools}"
                    )
            elif registered_write_tools:
                raise SmokeFailure(
                    f"MCP server registered write tools without opt-in auth: {registered_write_tools}"
                )

            await _check_mcp_resources(session, args.env)

            await _call_tool_json(session, "get_platform_health", {})
            await _call_tool_json(session, "get_dashboard_links", {})
            spans = await _call_tool_json(session, "get_run_trace_spans", {"run_id": args.run_id})
            span_rows = spans.get("spans") if isinstance(spans, dict) else None
            if not isinstance(span_rows, list):
                raise SmokeFailure(f"get_run_trace_spans returned unexpected payload: {spans}")
            if args.require_run_spans and not span_rows:
                raise SmokeFailure(f"run {args.run_id!r} returned no trace spans")

            filtered_spans = await _call_tool_json(
                session,
                "get_trace_spans",
                {"run_id": args.run_id, "limit": 25},
            )
            filtered_span_rows = (
                filtered_spans.get("spans") if isinstance(filtered_spans, dict) else None
            )
            if not isinstance(filtered_span_rows, list):
                raise SmokeFailure(f"get_trace_spans returned unexpected payload: {filtered_spans}")
            if args.require_run_spans and not filtered_span_rows:
                raise SmokeFailure(f"filtered trace query for {args.run_id!r} returned no spans")

            tree = await _call_tool_json(
                session,
                "render_trace_spans_tree",
                {"run_id": args.run_id, "limit": 25},
            )
            if not isinstance(tree, dict) or not isinstance(tree.get("tree"), str):
                raise SmokeFailure(f"render_trace_spans_tree returned unexpected payload: {tree}")

            if args.asset_key:
                history = await _call_tool_json(
                    session,
                    "get_materialization_history",
                    {"asset_key_path": args.asset_key, "env": args.env},
                )
                events = history.get("events") if isinstance(history, dict) else None
                if not isinstance(events, list):
                    raise SmokeFailure(
                        f"get_materialization_history returned unexpected payload: {history}"
                    )
                if args.require_materialization and not events:
                    raise SmokeFailure(f"asset {args.asset_key!r} returned no materializations")

                if args.exercise_write_tools or args.exercise_live_write_tools:
                    if not args.enable_write_tools or not args.api_token:
                        raise SmokeFailure(
                            "write-tool smoke requires --enable-write-tools and --api-token"
                        )
                    materialize = await _call_tool_json(
                        session,
                        "materialize_asset",
                        {
                            "asset_key_path": args.asset_key,
                            "env": args.env,
                            "job_name": args.job_name,
                            "dry_run": not args.exercise_live_write_tools,
                            "idempotency_key": "phlo-mcp-smoke-materialize",
                        },
                    )
                    audit = (
                        materialize.get("audit_context") if isinstance(materialize, dict) else None
                    )
                    if (
                        not isinstance(audit, dict)
                        or audit.get("dry_run") is args.exercise_live_write_tools
                    ):
                        raise SmokeFailure(
                            f"materialize_asset returned unexpected audit context: {materialize}"
                        )


async def _check_mcp_resources(session: ClientSession, env: str) -> None:
    resources = await session.list_resources()
    resource_uris = {str(resource.uri) for resource in resources.resources}
    expected_resources = {
        "phlo://runtime/config",
        "phlo://runtime/services",
        "phlo://runtime/plugins",
        "phlo://runtime/contracts",
        "phlo://runtime/dashboards",
    }
    missing_resources = sorted(expected_resources - resource_uris)
    if missing_resources:
        raise SmokeFailure(f"MCP server is missing expected resources: {missing_resources}")

    templates = await session.list_resource_templates()
    template_uris = {template.uriTemplate for template in templates.resourceTemplates}
    expected_templates = {
        "phlo://docs/packages/{package_name}",
        "phlo://runtime/services/{service_name}",
        "phlo://runtime/environments/{env}/assets",
        "phlo://runtime/environments/{env}/assets/{asset_id}",
        "phlo://runtime/environments/{env}/schemas/{asset_id}",
        "phlo://runtime/contracts/{table_name}",
    }
    missing_templates = sorted(expected_templates - template_uris)
    if missing_templates:
        raise SmokeFailure(
            f"MCP server is missing expected resource templates: {missing_templates}"
        )

    await _read_resource_json(session, "phlo://runtime/config")
    assets = await _read_resource_json(session, f"phlo://runtime/environments/{env}/assets")
    if not isinstance(assets, dict) or not isinstance(assets.get("items"), list):
        raise SmokeFailure(f"environment assets resource returned unexpected payload: {assets}")
    services = await _read_resource_json(session, "phlo://runtime/services")
    if not isinstance(services, list):
        raise SmokeFailure(f"phlo://runtime/services returned unexpected payload: {services}")
    docs = await session.read_resource("phlo://docs/packages/phlo-mcp")
    text = getattr(docs.contents[0], "text", "") if docs.contents else ""
    if "# phlo-mcp" not in text:
        raise SmokeFailure("phlo://docs/packages/phlo-mcp did not return package docs")


async def _read_resource_json(session: ClientSession, uri: str) -> dict[str, Any] | list[Any]:
    result = await session.read_resource(uri)
    if not result.contents:
        raise SmokeFailure(f"MCP resource {uri} returned no content")
    text = getattr(result.contents[0], "text", None)
    if not isinstance(text, str):
        raise SmokeFailure(f"MCP resource {uri} returned non-text content: {result.contents[0]}")
    payload = json.loads(text)
    if isinstance(payload, dict) and payload.get("error"):
        raise SmokeFailure(f"MCP resource {uri} returned upstream error: {payload}")
    return payload


async def _call_tool_json(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | list[Any]:
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise SmokeFailure(f"MCP tool {name} returned an error: {result.content}")
    if not result.content:
        raise SmokeFailure(f"MCP tool {name} returned no content")
    content = result.content[0]
    text = getattr(content, "text", None)
    if not isinstance(text, str):
        raise SmokeFailure(f"MCP tool {name} returned non-text content: {content}")
    payload = json.loads(text)
    if isinstance(payload, dict) and payload.get("payload", {}).get("error"):
        raise SmokeFailure(f"MCP tool {name} returned upstream error: {payload}")
    if isinstance(payload, dict):
        spans = payload.get("spans")
        if isinstance(spans, dict) and spans.get("error"):
            raise SmokeFailure(f"MCP tool {name} returned upstream error: {payload}")
        events = payload.get("events")
        if isinstance(events, dict) and events.get("error"):
            raise SmokeFailure(f"MCP tool {name} returned upstream error: {payload}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
