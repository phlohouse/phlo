"""Typed MCP response models for Phlo tool contracts.

Read tools answer through ToolEnvelope and guarded write tools through
WriteToolEnvelope, both reporting failures as structured ErrorPayload instead
of raw HTTP exceptions. ToolContract and PromptContract back tool
self-introspection, including the scope a tool requires.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorPayload(BaseModel):
    """Structured tool error payload returned instead of raw HTTP exceptions."""

    code: str
    message: str
    hint: str | None = None
    docs_url: str | None = None
    retryable: bool = False


class ToolEnvelope(BaseModel):
    """Common MCP response envelope for API-backed tools."""

    api_base_url: str | None = None
    payload: Any = None
    error: ErrorPayload | None = None


class AuditContext(BaseModel):
    """Audit metadata emitted by guarded write tools."""

    operation: str
    target: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool
    authenticated: bool
    api_base_url: str


class WriteToolEnvelope(BaseModel):
    """Common MCP response envelope for guarded write tools."""

    audit_context: AuditContext
    payload: Any = None
    error: ErrorPayload | None = None


class ToolContract(BaseModel):
    """Self-introspection record for one registered MCP tool."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required_scope: str | None = None


class PromptContract(BaseModel):
    """Self-introspection record for one registered MCP prompt."""

    name: str
    description: str | None = None
