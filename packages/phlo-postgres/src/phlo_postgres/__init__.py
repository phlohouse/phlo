"""Phlo PostgreSQL metadata store package.

This package provides the PostgreSQL integration for Phlo, including:
- Service plugin for managing PostgreSQL containers
- Resource management with connection pooling
- CLI commands for database operations
- Configuration settings management
- Publish targets for serving data

Example:
    >>> from phlo_postgres import PostgresResource, get_settings
    >>> settings = get_settings()
    >>> with PostgresResource() as db:
    ...     rows = db.query("SELECT * FROM users LIMIT 10")

"""

from phlo_postgres.plugin import PostgresServicePlugin
from phlo_postgres.publish_target import PostgresPublishTarget
from phlo_postgres.resource import PostgresResource
from phlo_postgres.settings import PostgresSettings, get_settings
from phlo_postgres.settings_store import PostgresSettingsStore

__all__ = [
    "PostgresPublishTarget",
    "PostgresResource",
    "PostgresServicePlugin",
    "PostgresSettings",
    "PostgresSettingsStore",
    "get_settings",
]
__version__ = "0.14.0"
