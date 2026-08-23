"""PostgreSQL publish target for data serving.

This module provides the PostgresPublishTarget class which serves as the
interface for publishing data to PostgreSQL. It wraps the PostgresResource
and provides configuration for the target schema where published data is stored.

The publish target is used by the phlo publishing system to route data to
PostgreSQL serving tables, typically in a "marts" or analytics schema.

Example:
    >>> from phlo_postgres import PostgresPublishTarget
    >>> target = PostgresPublishTarget()
    >>> print(target.default_schema)
    marts
    >>>
    >>> # Access the underlying resource for direct database operations
    >>> with target.resource as db:
    ...     db.execute("CREATE TABLE IF NOT EXISTS marts.summary (...)")

"""

from __future__ import annotations

from dataclasses import dataclass, field

from phlo_postgres.resource import PostgresResource
from phlo_postgres.settings import get_settings


@dataclass
class PostgresPublishTarget:
    """Structured publish target for PostgreSQL serving tables.

    Encapsulates the database resource and configuration for the target
    schema where published mart tables are stored; the default schema comes
    from the postgres_mart_schema setting (typically "marts").

    Example:
        >>> target = PostgresPublishTarget()
        >>> print(target.target_system)
        postgres
        >>> print(target.default_schema)
        marts
        >>>
        >>> # Custom resource
        >>> from phlo_postgres import PostgresResource
        >>> custom_resource = PostgresResource(host="prod.db.internal")
        >>> custom_target = PostgresPublishTarget(resource=custom_resource)

    """

    resource: PostgresResource = field(default_factory=PostgresResource)
    target_system: str = "postgres"

    @property
    def default_schema(self) -> str:
        """Return the default serving schema for published mart tables.

        Example:
            >>> target = PostgresPublishTarget()
            >>> schema = target.default_schema
            >>> print(f"Publishing to schema: {schema}")
            Publishing to schema: marts
            >>>
            >>> # Using the schema in DDL
            >>> with target.resource as db:
            ...     db.ensure_schema(schema)
            ...     db.execute(f"CREATE TABLE {schema}.users (...)")

        """
        return get_settings().postgres_mart_schema
