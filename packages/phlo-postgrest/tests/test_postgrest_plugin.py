"""PostgREST generated service contracts.

Locks the service definition to a pinned upstream image, project Postgres
credentials in PGRST_DB_URI (never a template fallback password), the bundled
config mount, and the pgrep-based healthcheck.
"""

from phlo_postgrest.plugin import PostgrestServicePlugin


def test_postgrest_uses_pinned_upstream_image() -> None:
    definition = PostgrestServicePlugin().service_definition

    assert definition["image"] == (
        "postgrest/postgrest:v14.15@"
        "sha256:2f8e7b656f09db697a8875177694b417b35cb76c21370de07fc54e711e902326"
    )
    assert "build" not in definition


def test_postgrest_uses_the_project_postgres_credentials() -> None:
    """Generated projects use a random database password, not the template fallback."""
    definition = PostgrestServicePlugin().service_definition

    assert definition["compose"]["environment"]["PGRST_DB_URI"] == (
        "postgresql://${POSTGRES_USER:-phlo}:${POSTGRES_PASSWORD:-phlo}@postgres:5432/"
        "${POSTGRES_DB:-phlo}"
    )
    assert "POSTGREST_VERSION" not in definition["env_vars"]
    assert {"source": "conf", "dest": "postgrest/conf"} in definition["files"]
    assert definition["compose"]["healthcheck"]["test"] == [
        "CMD",
        "pgrep",
        "postgrest",
    ]
