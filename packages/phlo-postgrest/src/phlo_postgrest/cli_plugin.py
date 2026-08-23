"""Postgrest CLI plugin registration.

Declares the PostgREST CLI command plugin through the shared CLI plugin
factory; registration metadata only.

Loaded through the phlo plugins.cli entry point at startup rather than imported directly.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_postgrest.cli import postgrest


PostgrestCliPlugin = cli_command_plugin_class(
    "PostgrestCliPlugin",
    name="postgrest",
    version="0.1.0",
    description="PostgREST CLI commands and helpers",
    commands=[postgrest],
)
