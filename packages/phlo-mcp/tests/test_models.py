"""Contract tests for MCP typed tool schemas.

Every registered tool must carry input and output schemas, the tools resource
must expose required scopes, and API errors must preserve their detail and hint.
"""

from __future__ import annotations

import httpx

from phlo_mcp.config import McpConfig
from phlo_mcp.errors import map_httpx_error
from phlo_mcp.models import ToolContract, ToolEnvelope, WriteToolEnvelope
from phlo_mcp.server import create_server


def test_response_models_export_json_schema() -> None:
    assert ToolEnvelope.model_json_schema()["type"] == "object"
    assert WriteToolEnvelope.model_json_schema()["type"] == "object"
    assert ToolContract.model_json_schema()["properties"]["output_schema"]["anyOf"]


def test_every_registered_tool_has_input_and_output_schema() -> None:
    server = create_server(McpConfig(api_token="secret", enable_write_tools=True))

    for tool in server._tool_manager.list_tools():
        assert tool.parameters, tool.name
        assert tool.output_schema, tool.name
        assert tool.output_schema.get("type") == "object", tool.name


def test_mcp_tools_resource_exposes_output_schema_and_required_scope() -> None:
    server = create_server(McpConfig(api_token="secret", enable_write_tools=True))
    resource = server._resource_manager._resources["phlo://docs/mcp/tools"]

    contracts = resource.fn()

    materialize = next(item for item in contracts if item["name"] == "materialize_asset")
    create_workflow = next(item for item in contracts if item["name"] == "create_workflow")

    assert materialize["output_schema"]["type"] == "object"
    assert materialize["required_scope"] == "lakehouse:operate"
    assert create_workflow["required_scope"] == "project:write"


def test_http_status_errors_preserve_api_detail() -> None:
    request = httpx.Request("POST", "http://test/api/authoring/workflows")
    response = httpx.Response(
        409,
        json={
            "detail": {
                "error": "workflow_already_exists",
                "message": "Files already exist:\n  - workflows/ingestion/demo/orders.py",
            }
        },
        request=request,
    )

    error = map_httpx_error(httpx.HTTPStatusError("conflict", request=request, response=response))

    assert error.code == "phlo.api.conflict"
    assert error.message == "Files already exist:\n  - workflows/ingestion/demo/orders.py"
    assert error.hint == "workflow_already_exists"
