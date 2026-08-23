"""PostgREST authentication infrastructure for Phlo.

This module provides the core authentication infrastructure for PostgREST,
including database roles, JWT functions, and user management. It serves as
the primary entry point for setting up PostgREST authentication in a Phlo
project.

Example:
    >>> from phlo_postgrest import setup_postgrest
    >>> setup_postgrest()  # Sets up auth infrastructure
    >>> setup_postgrest(force=True)  # Re-apply even if already set up

Note:
    This package requires a running PostgreSQL instance with appropriate
    superuser privileges to create roles and schemas.

"""

from phlo_postgrest.setup import setup_postgrest

__all__ = ["setup_postgrest"]
