"""Hasura hooks for auto-configuration.

This module provides hook functions for automatically configuring Hasura
during project initialization or deployment. It handles environment loading
and table tracking operations.

The hooks are designed to be called from the phlo CLI or programmatically
to automate Hasura setup.

Example:
    $ python -m phlo_hasura.hooks track-tables auto
    $ python -m phlo_hasura.hooks track-tables api,marts

Functions:
    track_tables: Auto-track tables in the specified schema(s).
    _load_env_files: Load environment variables from .phlo/.env files.

Logs through phlo.logging and performs table tracking via phlo_hasura.track.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from phlo.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _load_env_files() -> None:
    """Load environment variables from .phlo/.env and .phlo/.env.local.

    Attempts to load environment variables using python-dotenv if available,
    falling back to manual parsing if dotenv is not installed.

    Files are loaded in order:
        1. .phlo/.env
        2. .phlo/.env.local (overrides .env)

    Failures are silently ignored; .env.local values override .env values.

    Example:
        >>> _load_env_files()
        # Environment variables are now loaded from .phlo/.env files

    """
    try:
        from dotenv import load_dotenv

        phlo_dir = Path.cwd() / ".phlo"
        env_file = phlo_dir / ".env"
        env_local = phlo_dir / ".env.local"

        if env_file.exists():
            load_dotenv(env_file)
        if env_local.exists():
            load_dotenv(env_local, override=True)
    except ImportError:
        # dotenv not available, try manual parsing
        phlo_dir = Path.cwd() / ".phlo"
        for env_file in [phlo_dir / ".env", phlo_dir / ".env.local"]:
            if env_file.exists():
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            # Remove quotes if present
                            value = value.strip().strip('"').strip("'")
                            os.environ.setdefault(key.strip(), value)


def track_tables(schemas: str = "api") -> None:
    """Auto-track tables in the specified schema(s).

    Automatically discovers and tracks all tables in the specified schemas.
    Can track multiple schemas at once or auto-discover all user schemas.

    ``schemas`` is a comma-separated list (e.g. "marts,api") or "auto" to
    discover all user schemas; failures during auto-tracking are propagated.

    Example:
        >>> track_tables("api")  # Track single schema
        >>> track_tables("marts,api")  # Track multiple schemas
        >>> track_tables("auto")  # Auto-discover all schemas

    """
    from phlo_hasura.track import auto_track, auto_track_all

    if schemas == "auto":
        logger.info("Auto-discovering all user schemas...")
        try:
            result = auto_track_all(verbose=True)
            logger.info("Auto-discovery complete: %d schemas processed", len(result))
        except Exception as e:
            logger.error("Failed to auto-track tables: %s", e)
            raise
    else:
        schema_list = [s.strip() for s in schemas.split(",") if s.strip()]
        for schema in schema_list:
            logger.info("Auto-tracking tables in schema: %s", schema)
            try:
                result = auto_track(schema=schema, verbose=True)
                logger.info("Tracking complete for %s: %s", schema, result)
            except Exception as e:
                logger.error("Failed to auto-track tables in schema %s: %s", schema, e)
                # Continue with other schemas even if one fails


if __name__ == "__main__":
    setup_logging()

    # Load env files before running hooks
    _load_env_files()

    if len(sys.argv) > 1 and sys.argv[1] == "track-tables":
        schemas = sys.argv[2] if len(sys.argv) > 2 else "auto"
        track_tables(schemas=schemas)
    else:
        logger.info("Usage: python -m phlo_hasura.hooks track-tables [schemas]")
        logger.info("  schemas: comma-separated list (e.g., 'marts,api'), or 'auto' to discover")
