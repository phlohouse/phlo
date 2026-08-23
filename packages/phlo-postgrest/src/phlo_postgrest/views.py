"""PostgREST API view generation from dbt models.

Parses dbt's manifest.json, generates CREATE VIEW statements, and manages
database permissions based on dbt tags for applying or diffing view changes.

Example:
    >>> from phlo_postgrest.views import generate_views
    >>> generate_views(apply=True, models="mrt_*")
    Views applied successfully

"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from phlo.config.base import BaseConfig
from phlo.config.network import resolve_host
from phlo.logging import get_logger
from pydantic import Field

logger = get_logger(__name__)


class PostgrestViewsSettings(BaseConfig):
    """Configuration settings for PostgREST view generation.

    Loads settings from environment variables and configuration files,
    controlling paths, database connections, and schema selection.

    Example:
        >>> settings = PostgrestViewsSettings()
        >>> settings.postgres_host
        'postgres'

    """

    dbt_manifest_path: str = Field(
        default="workflows/transforms/dbt/target/manifest.json",
        description="Path to dbt manifest.json",
    )
    dbt_api_source_schema: str | None = Field(
        default=None,
        description="dbt schema to expose through generated PostgREST views",
    )
    postgres_host: str = Field(default="postgres", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_user: str = Field(default="phlo", description="PostgreSQL username")
    postgres_password: str | None = Field(
        default=None,
        description="PostgreSQL password (required; set PHLO_POSTGRES_PASSWORD or POSTGRES_PASSWORD env var)",
    )
    postgres_db: str = Field(default="phlo", description="PostgreSQL database name")

    def model_post_init(self, __context: object) -> None:
        host, port = resolve_host(
            self.postgres_host,
            self.postgres_port,
            port_env_var="POSTGRES_PORT",
        )
        object.__setattr__(self, "postgres_host", host)
        object.__setattr__(self, "postgres_port", port)


@dataclass
class DbtModel:
    """A dbt model extracted from manifest.json.

    Example:
        >>> model = DbtModel(
        ...     name="mrt_orders",
        ...     schema="marts",
        ...     description="Order metrics",
        ...     columns={"order_id": {...}},
        ...     tags=["analyst"],
        ...     unique_id="model.phlo.mrt_orders"
        ... )

    """

    name: str
    schema: str
    description: str
    columns: dict
    tags: list[str]
    unique_id: str


class DbtManifestParser:
    """Parse model metadata out of dbt manifest.json files, with schema
    filtering and dependency graph construction for view generation and ordering.

    Example:
        >>> parser = DbtManifestParser(
        ...     manifest_path="target/manifest.json",
        ...     source_schema="marts"
        ... )
        >>> models = parser.parse()

    """

    def __init__(self, manifest_path: Optional[str] = None, source_schema: Optional[str] = None):
        """Initialize parser; paths default to settings values when omitted.

        Raises FileNotFoundError when the manifest does not exist at the
        given path.

        Example:
            >>> parser = DbtManifestParser(
            ...     "workflows/transforms/dbt/target/manifest.json",
            ...     "marts"
            ... )

        """
        settings = PostgrestViewsSettings()
        if manifest_path is None:
            manifest_path = settings.dbt_manifest_path
        if source_schema is None:
            source_schema = settings.dbt_api_source_schema

        self.manifest_path = Path(manifest_path)
        self.source_schema = source_schema

        if not self.manifest_path.exists():
            raise FileNotFoundError(f"dbt manifest not found at {manifest_path}")

    def parse(self) -> dict[str, DbtModel]:
        """Parse manifest and return filtered models keyed by name.

        Raises FileNotFoundError when the manifest is missing and
        json.JSONDecodeError when it contains invalid JSON.

        Example:
            >>> parser = DbtManifestParser(source_schema="marts")
            >>> models = parser.parse()
            >>> list(models.keys())
            ['mrt_orders', 'mrt_customers']

        """
        with open(self.manifest_path) as f:
            manifest = json.load(f)

        source_schema = self.source_schema or self._infer_source_schema(manifest)

        models = {}
        for unique_id, node in manifest.get("nodes", {}).items():
            if not unique_id.startswith("model."):
                continue

            if node.get("schema") != source_schema:
                continue

            model = DbtModel(
                name=node.get("name"),
                schema=node.get("schema"),
                description=node.get("description", ""),
                columns=node.get("columns", {}),
                tags=node.get("tags", []),
                unique_id=unique_id,
            )

            models[model.name] = model

        return models

    def _infer_source_schema(self, manifest: dict) -> str:
        """Infer source schema when not explicitly configured; assumes a single
        schema across manifest models.

        Raises ValueError when multiple schemas exist without explicit
        configuration.

        Example:
            >>> schema = parser._infer_source_schema(manifest)
            >>> print(schema)
            'marts'

        """
        schemas = {
            node.get("schema")
            for unique_id, node in manifest.get("nodes", {}).items()
            if unique_id.startswith("model.") and isinstance(node.get("schema"), str)
        }
        if len(schemas) == 1:
            return next(iter(schemas))
        raise ValueError(
            "dbt_api_source_schema is not configured and manifest contains multiple model "
            f"schemas: {sorted(schemas)}"
        )

    def build_dependency_graph(self) -> dict[str, list[str]]:
        """Build a model-name-to-dependencies graph so view generation can
        create views in dependency order.

        Example:
            >>> graph = parser.build_dependency_graph()
            >>> graph.get("mrt_orders")
            ['stg_orders', 'stg_customers']

        """
        with open(self.manifest_path) as f:
            manifest = json.load(f)

        graph = {}
        for unique_id, node in manifest.get("nodes", {}).items():
            if not unique_id.startswith("model."):
                continue

            model_name = node.get("name")
            depends_on = []

            for dep_id in node.get("depends_on", {}).get("nodes", []):
                if dep_id.startswith("model."):
                    dep_name = dep_id.split(".")[-1]
                    depends_on.append(dep_name)

            graph[model_name] = depends_on

        return graph


class ViewGenerator:
    """Generate PostgREST-compatible database views from dbt models, including
    column ordering, SQL comments, tag-based permissions, and Row-Level
    Security policies.

    Example:
        >>> generator = ViewGenerator(api_schema="api")
        >>> sql = generator.generate_all_views(models="mrt_*")

    """

    def __init__(
        self,
        manifest_path: Optional[str] = None,
        api_schema: str = "api",
        source_schema: Optional[str] = None,
    ):
        """Initialize generator; paths and schemas default as in DbtManifestParser.

        Example:
            >>> generator = ViewGenerator(
            ...     manifest_path="target/manifest.json",
            ...     api_schema="api",
            ...     source_schema="marts"
            ... )

        """
        self.parser = DbtManifestParser(manifest_path, source_schema=source_schema)
        self.api_schema = api_schema

    def generate_view_sql(self, model: DbtModel) -> str:
        """Generate the complete CREATE OR REPLACE VIEW statement (column
        selection, table reference, SQL COMMENT) for a single model.

        Example:
            >>> sql = generator.generate_view_sql(model)
            >>> print(sql)
            CREATE OR REPLACE VIEW api.mrt_orders AS
            SELECT order_id, customer_id, total
            FROM marts.mrt_orders;

        """
        # Extract column names in order
        columns = list(model.columns.keys())
        column_list = ",\n    ".join(columns) if columns else "*"

        sql = f"""-- Auto-generated by phlo api generate-views
-- Model: {model.name}
-- Description: {model.description}

CREATE OR REPLACE VIEW {self.api_schema}.{model.name} AS
SELECT
    {column_list}
FROM {model.schema}.{model.name};

COMMENT ON VIEW {self.api_schema}.{model.name} IS '{self._escape_string(model.description)}';
"""
        return sql

    def generate_permissions_sql(self, model: DbtModel) -> str:
        """Generate GRANT statements for a model by mapping dbt tags to roles
        ('public' -> anon, 'analyst' -> analyst/admin, 'admin' -> admin).

        Example:
            >>> sql = generator.generate_permissions_sql(model)
            >>> print(sql)
            GRANT SELECT ON api.mrt_orders TO analyst;
            GRANT SELECT ON api.mrt_orders TO admin;

        """
        sql_parts = ["\n-- Permissions"]

        # Map tags to roles
        role_mapping = {
            "public": ["anon"],
            "analyst": ["analyst", "admin"],
            "admin": ["admin"],
        }

        granted_roles = set()
        for tag in model.tags:
            for role in role_mapping.get(tag, []):
                if role not in granted_roles:
                    sql_parts.append(f"GRANT SELECT ON {self.api_schema}.{model.name} TO {role};")
                    granted_roles.add(role)

        if granted_roles:
            sql_parts.append("\n-- Note: PostgreSQL views do not support RLS policies directly.")
            sql_parts.append(
                "-- Apply row-level controls to underlying tables or security-definer functions."
            )

        return "\n".join(sql_parts)

    def generate_all_views(self, model_filter: Optional[str] = None) -> str:
        """Generate a complete SQL script (views plus permissions) for all
        manifest models matching the glob filter, in dependency order;
        returns an empty string when nothing matches.

        Example:
            >>> sql = generator.generate_all_views(model_filter="mrt_*")
            >>> len(sql) > 0
            True

        """
        models = self.parser.parse()

        # Filter models
        if model_filter:
            from fnmatch import fnmatch

            models = {name: model for name, model in models.items() if fnmatch(name, model_filter)}

        if not models:
            return ""

        # Sort by dependencies (simple topological sort)
        dependency_graph = self.parser.build_dependency_graph()
        sorted_models = self._topological_sort(models, dependency_graph)

        sql_parts = [
            "-- PostgREST API Views",
            f"-- Generated from dbt manifest at {self.parser.manifest_path}",
            f"-- Schema: {self.api_schema}",
            "--\n",
        ]

        for model_name in sorted_models:
            model = models[model_name]
            sql_parts.append(self.generate_view_sql(model))
            sql_parts.append(self.generate_permissions_sql(model))
            sql_parts.append("\n")

        return "\n".join(sql_parts)

    def _topological_sort(
        self, models: dict[str, DbtModel], graph: dict[str, list[str]]
    ) -> list[str]:
        """Sort model names topologically so dependencies come first.

        Example:
            >>> sorted_names = generator._topological_sort(models, graph)
            >>> sorted_names[0]  # Least dependent model first
            'stg_orders'

        """
        visited = set()
        order = []

        def visit(name: str) -> None:
            """Visit dependencies before the model itself."""
            if name in visited:
                return
            visited.add(name)

            for dep in graph.get(name, []):
                if dep in models:
                    visit(dep)

            if name in models:
                order.append(name)

        for name in models:
            visit(name)

        return order

    @staticmethod
    def _escape_string(s: str) -> str:
        """Escape single quotes for safe use in SQL COMMENT statements.

        Example:
            >>> ViewGenerator._escape_string("It's a test")
            "It''s a test"

        """
        return s.replace("'", "''")


class PostgreSTViewManager:
    """Manage PostgreSQL view operations: connections, SQL execution, view
    discovery, and diff generation for PostgREST view management.

    Example:
            >>> manager = PostgreSTViewManager(host="postgres", port=5432)
            >>> manager.execute_sql("CREATE VIEW test AS SELECT 1")

    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """Initialize the connection manager; unset parameters fall back to
        PostgrestViewsSettings.

        Raises ValueError when no password is available via argument or
        environment.

        Example:
            >>> manager = PostgreSTViewManager(
            ...     host="db.example.com",
            ...     port=5432,
            ...     database="phlo"
            ... )

        """
        settings = PostgrestViewsSettings()
        self.host, self.port = resolve_host(
            host or settings.postgres_host,
            int(port or settings.postgres_port),
            port_env_var="POSTGRES_PORT",
        )
        self.database = database or settings.postgres_db
        self.user = user or settings.postgres_user
        self.password = password or settings.postgres_password
        if not self.password:
            raise ValueError(
                "PostgreSQL password must be set via the PHLO_POSTGRES_PASSWORD "
                "or POSTGRES_PASSWORD environment variable, or passed as the 'password' argument."
            )

    def get_connection(self):
        """Open a new autocommit psycopg2 connection for DDL execution.

        Raises psycopg2.Error when the connection fails due to network or
        authentication issues.

        Example:
            >>> conn = manager.get_connection()
            >>> cursor = conn.cursor()
            >>> cursor.execute("SELECT 1")

        """
        conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn

    def execute_sql(self, sql: str, verbose: bool = True) -> None:
        """Execute SQL against the database, logging progress when verbose.

        Re-raises (after logging) any exception raised by SQL execution.

        Example:
            >>> manager.execute_sql("CREATE VIEW test AS SELECT 1")
            ✓ SQL executed successfully

        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            if verbose:
                logger.info("Executing %s characters of SQL...", len(sql))
            cursor.execute(sql)
            if verbose:
                logger.info("✓ SQL executed successfully")
        except Exception as e:
            logger.error("✗ SQL execution failed: %s", e)
            raise
        finally:
            cursor.close()
            conn.close()

    def get_existing_views(self, schema: str = "api") -> set[str]:
        """Return the names of existing views in a schema, queried from
        information_schema.tables.

        Example:
            >>> views = manager.get_existing_views("api")
            >>> print(views)
            {'mrt_orders', 'mrt_customers'}

        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'VIEW'
            """,
                (schema,),
            )
            return {row[0] for row in cursor.fetchall()}
        finally:
            cursor.close()
            conn.close()

    def generate_diff(self, new_sql: str, schema: str = "api") -> str:
        """Compare deployed views in a schema against newly generated SQL to
        report created, updated, and orphaned views.

        Example:
            >>> diff = manager.generate_diff(sql, "api")
            >>> print(diff)
            Views to be created/updated:
              mrt_orders (updated)
              mrt_customers (new)

        """
        existing_views = self.get_existing_views(schema)

        # Parse generated SQL to find new views
        import re

        new_views = set(re.findall(rf"CREATE OR REPLACE VIEW {schema}\.(\w+)", new_sql))

        lines = ["Views to be created/updated:"]
        for view in sorted(new_views):
            status = "(updated)" if view in existing_views else "(new)"
            lines.append(f"  {view} {status}")

        removed = existing_views - new_views
        if removed:
            lines.append("Views to be removed:")
            for view in sorted(removed):
                lines.append(f"  {view} (orphaned)")

        return "\n".join(lines)


def generate_views(
    output: Optional[str] = None,
    apply: bool = False,
    diff: bool = False,
    models: Optional[str] = None,
    manifest_path: Optional[str] = None,
    api_schema: str = "api",
    source_schema: Optional[str] = None,
    verbose: bool = True,
) -> str:
    """Generate PostgREST API views from dbt models; the main workflow entry
    point orchestrating manifest parsing, SQL generation, and optional
    application or diffing.

    Output modes: return the SQL string by default, write it to `output`,
    execute it with `apply`, or show a comparison with `diff`. Returns an
    empty string when no models match the filter. Database failures during
    apply propagate to the caller.

    Example:
            >>> # Generate SQL to stdout
            >>> sql = generate_views()

            >>> # Apply directly to database
            >>> result = generate_views(apply=True, models="mrt_*")
            >>> print(result)
            Views applied successfully

            >>> # Show what's changing
            >>> diff = generate_views(diff=True)
            >>> print(diff)

    """
    if verbose:
        logger.info("=" * 60)
        logger.info("PostgREST API View Generation")
        logger.info("=" * 60)

    # Generate SQL
    generator = ViewGenerator(manifest_path, api_schema, source_schema=source_schema)
    sql = generator.generate_all_views(models)

    if not sql:
        if verbose:
            logger.info("No models found matching filter")
        return ""

    if verbose:
        logger.info("Generated SQL for %s characters", len(sql))

    # Handle diff
    if diff:
        manager = PostgreSTViewManager()
        diff_output = manager.generate_diff(sql, api_schema)
        if verbose:
            logger.info("\n%s", diff_output)
        return diff_output

    # Handle apply
    if apply:
        if verbose:
            logger.info("Applying to database...")
        view_names = sorted(
            set(re.findall(rf"CREATE OR REPLACE VIEW {re.escape(api_schema)}\.(\w+)", sql))
        )
        logger.info(
            "postgrest_view_apply_started",
            schema=api_schema,
            view_count=len(view_names),
            view_names=view_names,
        )
        manager = PostgreSTViewManager()
        try:
            manager.execute_sql(sql, verbose=verbose)
        except Exception:
            logger.exception(
                "postgrest_view_apply_failed",
                schema=api_schema,
                view_count=len(view_names),
                view_names=view_names,
            )
            raise
        logger.info(
            "postgrest_view_apply_succeeded",
            schema=api_schema,
            view_count=len(view_names),
            view_names=view_names,
        )
        return "Views applied successfully"

    # Handle output file
    if output:
        output_path = Path(output)
        output_path.write_text(sql)
        if verbose:
            logger.info("✓ SQL written to %s", output)
        return f"SQL written to {output}"

    # Default: print to stdout
    return sql
