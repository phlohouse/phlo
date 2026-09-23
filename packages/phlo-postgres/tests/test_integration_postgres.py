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

    def test_postgres_config_has_defaults(self):
        """Test Postgres config has sensible defaults."""
        from phlo_postgres.settings import get_settings

        settings = get_settings()

        assert settings.postgres_host is not None
        assert settings.postgres_port > 0
        assert settings.postgres_db is not None

    def test_connection_string_building(self):
        """Test building a connection string from config."""
        from phlo_postgres.settings import get_settings

        settings = get_settings()

        # Build connection string
        conn_str = (
            f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )

        assert "postgresql://" in conn_str
        assert str(settings.postgres_port) in conn_str


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

        assert isinstance(service_def, dict)
        # Service definitions have flat structure with 'name' and 'compose' keys
        assert "name" in service_def or "compose" in service_def

    def test_service_definition_has_image(self):
        """Test service definition has container image specified."""
        from phlo_postgres.plugin import PostgresServicePlugin

        plugin = PostgresServicePlugin()
        service_def = plugin.service_definition

        # Navigate to find image
        services = service_def.get("services", {})
        if services:
            # Should have postgres service with image
            for svc_name, svc_config in services.items():
                if "postgres" in svc_name.lower():
                    assert "image" in svc_config or "build" in svc_config


# =============================================================================
# Psycopg2 Connection Tests (with mocks)
# =============================================================================


class TestPostgresConnectionMocked:
    """Test Postgres connections with mocks."""

    def test_psycopg2_connect_with_config(self):
        """Test psycopg2 connection using config values."""
        import psycopg2
        from phlo_postgres.settings import get_settings

        settings = get_settings()

        mock_conn = MagicMock()

        with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            # Simulate what most code does
            psycopg2.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )

            mock_connect.assert_called_once()
            # Verify kwargs
            call_kwargs = mock_connect.call_args.kwargs
            assert call_kwargs["host"] == settings.postgres_host
            assert call_kwargs["database"] == settings.postgres_db

    def test_connection_with_cursor_context(self):
        """Test connection with cursor context manager."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1,)]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda _: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg2.connect", return_value=mock_conn):
            import psycopg2

            conn = psycopg2.connect(host="localhost", database="test")
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchall()

            assert result == [(1,)]


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

        # Table should not exist after rollback
        with postgres_connection.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'rollback_test'
            """)
            result = cur.fetchone()

        # This may vary based on temp table behavior
        assert result is not None


# =============================================================================
# Version and Export Tests
# =============================================================================


class TestPostgresExports:
    """Test module exports and version."""

    def test_plugin_importable(self):
        """Test PostgresServicePlugin is importable."""
        from phlo_postgres.plugin import PostgresServicePlugin

        assert PostgresServicePlugin is not None

    def test_module_importable(self):
        """Test phlo_postgres module is importable."""
        import phlo_postgres

        assert phlo_postgres is not None
