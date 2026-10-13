"""Import-free root help for the bundled CLI extensions.

Only installed, enabled entry points contribute help. Third-party extensions
without a bundled description remain available by name and through ``commands``.
The command-contract test checks these descriptions against their Click owners.
"""

from phlo.config.settings import get_settings
from phlo.plugins.discovery._entry_points import entry_points_for_group
from phlo.plugins.discovery._plugin_loading import is_plugin_allowed

# Key by entry-point target, not its alias: an unrelated extension may reuse a name.
_HELP = {
    "phlo_openmetadata.cli_plugin:OpenMetadataCliPlugin": {
        "openmetadata": "Manage OpenMetadata integration (optional): check health and sync catalog tables and dbt documentation."
    },
    "phlo_postgrest.cli_plugin:PostgrestCliPlugin": {
        "postgrest": "PostgREST API management commands."
    },
    "phlo_dbt.cli_plugin:DbtCliPlugin": {"dbt": "Dbt commands (compile, run, test, publishing)."},
    "phlo_polaris.cli_plugin:PolarisCliPlugin": {
        "polaris": "Manage the Polaris catalog service (status, bootstrap, migration)."
    },
    "phlo_sling.cli_plugin:SlingCliPlugin": {"sling": "Sling replication commands."},
    "phlo_airbyte.cli_plugin:AirbyteCliPlugin": {
        "airbyte": "Interact with the Airbyte control plane (status, connections, sync)."
    },
    "phlo_pandera.cli_plugin:PanderaCliPlugin": {
        "schema": "Manage Pandera schemas and schema validation.",
        "validate-schema": "Validate a Pandera schema file for valid DataFrameModel syntax, field descriptions, constraints, and type annotations; exits 0 when valid and 1 when issues are found.",
        "validate-workflow": "Validate a workflow asset file for decorator usage, unique_key presence, cron validity, function signature, and return types before deployment; exits 0 when valid and 1 when issues are found.",
    },
    "phlo_minio.cli_plugin:MinioCliPlugin": {
        "minio": "Run MinIO client (mc) commands inside the project service container."
    },
    "phlo_hasura.cli_plugin:HasuraCliPlugin": {"hasura": "Hasura GraphQL metadata management CLI."},
    "phlo_nessie.cli_plugin:NessieCliPlugin": {
        "catalog": "Manage the lakehouse catalog (Nessie-backed).",
        "branch": "Manage Nessie branches for data versioning.",
    },
    "phlo_dagster.cli_plugin:DagsterCliPlugin": {
        "dev": "Start the Dagster development server for your workflows.",
        "status": "Show current state of assets, jobs, and services: asset materialization status and freshness, service health (Dagster, Trino, MinIO, Nessie), with color-coded indicators.",
        "backfill": "Run asset materialization across a date range with parallel execution.",
        "materialize": "Materialize Dagster assets via the configured container backend.",
    },
    "phlo_lineage.cli_plugin:LineageCliPlugin": {
        "lineage": "Asset dependency and lineage visualization commands."
    },
    "phlo_kafka.cli_plugin:KafkaCliPlugin": {
        "kafka": "Interact with the Kafka broker (status, topics)."
    },
    "phlo_mcp.cli_plugin:McpCliPlugin": {"mcp": "Run and inspect the Phlo MCP server."},
    "phlo_trino.cli_plugin:TrinoCliPlugin": {
        "trino": "Run the Trino shell or a Trino-specific helper command."
    },
    "phlo_alerting.cli_plugin:AlertingCliPlugin": {"alerts": "Alert management and configuration."},
    "phlo_clickhouse.cli_plugin:ClickHouseCliPlugin": {
        "clickhouse": "Query and inspect the ClickHouse data plane service."
    },
    "phlo_postgres.cli_plugin:PostgresCliPlugin": {
        "postgres": "Run psql or PostgreSQL helper commands against the project database."
    },
    "phlo_clickstack.cli_plugin:ClickStackCliPlugin": {
        "clickstack": "Query and inspect the ClickStack service."
    },
}


def get_plugin_help() -> dict[str, str]:
    """Read installation metadata without loading a provider."""
    settings = get_settings()
    result: dict[str, str] = {}
    if not settings.plugins_enabled:
        return result
    for entry_point in entry_points_for_group("phlo.plugins.cli"):
        if not is_plugin_allowed(entry_point.name):
            continue
        for name, description in _HELP.get(entry_point.value, {}).items():
            result.setdefault(name, description)
    return result
