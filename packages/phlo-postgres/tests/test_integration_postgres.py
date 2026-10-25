"""Comprehensive integration tests for phlo-postgres.

Per TEST_STRATEGY.md Level 2 (Functional):
- Connection String Building: Test connection string construction
- DB Ops: Create user, create DB, verify connectivity
- Service Plugin: Verify plugin registration
"""

from unittest.mock import patch, MagicMock

import pytest

# =============================================================================
# Connection Configuration Tests
# =============================================================================


class TestPostgresConfiguration:
    """Test Postgres configuration and connection building."""

    def test_postgres_config_accessible(self):
        """Test Postgres configuration is accessible from phlo_postgres.settings."""
        from phlo_postgres.settings import get_settings

        settings = get_settings()

        assert hasattr(settings, "postgres_host")
        assert hasattr(settings, "postgres_port")
        assert hasattr(settings, "postgres_db")
        assert hasattr(settings, "postgres_user")

    def test_postgres_config_has_defaults(self, monkeypatch, tmp_path):
        """Test Postgres config has concrete, non-empty defaults."""
        from phlo_postgres.settings import PostgresSettings

        for name in (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_MART_SCHEMA",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1"):
            # Isolate from this workspace's .phlo/.env, which may set POSTGRES_PORT.
            settings = PostgresSettings(_project_root=tmp_path)

        assert settings.postgres_host == "postgres"
        assert settings.postgres_port == 5432
        assert settings.postgres_user == "phlo"
        assert settings.postgres_db == "phlo"
        assert settings.postgres_mart_schema == "marts"

    def test_connection_string_building(self):
        """Test the production connection string encodes credentials correctly."""
        from phlo_postgres.settings import PostgresSettings

        with patch("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1"):
            settings = PostgresSettings(
                postgres_host="db.internal",
                postgres_port=6543,
                postgres_user="svc",
                postgres_password="p@ss/word",
                postgres_db="lake",
            )

        assert settings.get_postgres_connection_string() == (
            "postgresql://svc:p%40ss%2Fword@db.internal:6543/lake"
        )
        assert settings.get_postgres_connection_string(include_db=False) == (
            "postgresql://svc:p%40ss%2Fword@db.internal:6543"
        )


# =============================================================================
# Service Plugin Tests
# =============================================================================


class TestPostgresServicePlugin:
    """Test Postgres service plugin."""

    def test_plugin_initializes(self):
        """Test PostgresServicePlugin can be instantiated."""
        from phlo_postgres.plugin import PostgresServicePlugin

        plugin = PostgresServicePlugin()
        assert plugin is not None

    def test_plugin_metadata(self):
        """Test plugin metadata is correctly defined."""
        from phlo_postgres.plugin import PostgresServicePlugin

        plugin = PostgresServicePlugin()
        metadata = plugin.metadata

        assert metadata.name == "postgres"
        assert metadata.version is not None

    def test_service_definition_loads(self):
        """Test service definition YAML can be loaded."""
        from phlo_postgres.plugin import PostgresServicePlugin

        plugin = PostgresServicePlugin()
        service_def = plugin.service_definition

        assert service_def["name"] == "postgres"
        assert service_def["category"] == "core"
        assert service_def["compose"]["healthcheck"]["test"][0] == "CMD-SHELL"

    def test_service_definition_has_image(self):
        """Test service definition pins the upstream postgres image."""
        from phlo_postgres.plugin import PostgresServicePlugin

        plugin = PostgresServicePlugin()
        service_def = plugin.service_definition

        assert service_def["image"].startswith("postgres:18.4-alpine3.24@sha256:")
        assert "build" not in service_def


# =============================================================================
# Psycopg2 Connection Tests (with mocks)
# =============================================================================


class TestPostgresConnectionMocked:
    """Test Postgres connections with mocks."""

    def test_psycopg2_connect_with_config(self):
        """The settings store connects with the DSN built from Postgres settings."""
        from phlo.capabilities.settings import SettingsScope
        from phlo_postgres.settings import PostgresSettings
        from phlo_postgres.settings_store import PostgresSettingsStore

        with patch("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1"):
            settings = PostgresSettings(
                postgres_host="db.internal",
                postgres_port=6543,
                postgres_user="svc",
                postgres_password="p@ss/word",
                postgres_db="lake",
            )

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with (
            patch("phlo_postgres.settings_store.get_postgres_settings", return_value=settings),
            patch("phlo_postgres.settings_store.ObservatorySettingsStorageConfig") as config,
            patch("psycopg2.connect", return_value=mock_conn) as mock_connect,
        ):
            config.return_value.observatory_settings_db_url = None
            store = PostgresSettingsStore()
            assert store.get(SettingsScope.GLOBAL, "demo") is None

        mock_connect.assert_called_once_with("postgresql://svc:p%40ss%2Fword@db.internal:6543/lake")


# =============================================================================
# Functional Integration Tests (Real Postgres if available)
# =============================================================================


@pytest.fixture
def postgres_connection():
    """Fixture providing a real Postgres connection if available."""
    from phlo_postgres.settings import get_settings
    import psycopg2

    settings = get_settings()

    try:
        conn = psycopg2.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            connect_timeout=5,
        )
    except psycopg2.OperationalError as e:
        if e.pgcode is not None:
            raise
        pytest.skip(f"Postgres not available: {e}")

    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.integration
class TestPostgresIntegrationReal:
    """Real integration tests against a running Postgres instance."""

    def test_simple_query(self, postgres_connection):
        """Test simple query execution against real Postgres."""
        with postgres_connection.cursor() as cur:
            cur.execute("SELECT 1 AS one")
            result = cur.fetchall()

        assert result == [(1,)]

    def test_version_query(self, postgres_connection):
        """Test querying Postgres version."""
        with postgres_connection.cursor() as cur:
            cur.execute("SELECT version()")
            result = cur.fetchone()

        assert result is not None
        assert "PostgreSQL" in result[0]

    def test_create_temp_table(self, postgres_connection):
        """Test creating and querying a temporary table."""
        with postgres_connection.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE test_table (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100)
                )
            """)
            cur.execute("INSERT INTO test_table (name) VALUES ('test1'), ('test2')")
            cur.execute("SELECT COUNT(*) FROM test_table")
            result = cur.fetchone()

        assert result[0] == 2

    def test_list_databases(self, postgres_connection):
        """Test listing databases."""
        with postgres_connection.cursor() as cur:
            cur.execute("""
                SELECT datname FROM pg_database
                WHERE datistemplate = false
            """)
            databases = [row[0] for row in cur.fetchall()]

        # Should have at least the connected database
        assert len(databases) >= 1

    def test_list_tables(self, postgres_connection):
        """Test listing tables in public schema."""
        with postgres_connection.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
            """)
            tables = [row[0] for row in cur.fetchall()]

        # May be empty if no tables
        assert isinstance(tables, list)

    def test_transaction_rollback(self, postgres_connection):
        """Test transaction rollback."""
        postgres_connection.autocommit = False

        with postgres_connection.cursor() as cur:
            cur.execute("CREATE TEMP TABLE rollback_test (id INT)")
            cur.execute("INSERT INTO rollback_test VALUES (1)")

        # Rollback
        postgres_connection.rollback()

        # The temporary table must be gone after rollback
        with postgres_connection.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'rollback_test'
            """)
            result = cur.fetchone()

        assert result[0] == 0


# =============================================================================
# Version and Export Tests
# =============================================================================


class TestPostgresExports:
    """Test module exports and version."""

    def test_plugin_importable(self):
        """Test PostgresServicePlugin is importable."""
        from phlo_postgres.plugin import PostgresServicePlugin

        assert PostgresServicePlugin is not None
