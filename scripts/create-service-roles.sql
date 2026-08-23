-- Service role creation for regulated deployments.
--
-- This script creates scoped service roles so that each service connects
-- with its own credentials instead of shared admin credentials.
--
-- Run the Trino section against Trino as admin.
-- Run the PostgreSQL section against the phlo metadata database as superuser.
--
-- After creating roles, set the corresponding env vars in .phlo/.env.local.
-- See docs/setup/service-credentials.md for the full guide.

-- ============================================================================
-- Trino roles
-- ============================================================================
-- Run against Trino (e.g., via trino-cli or JDBC as admin)

-- phlo-api: read-only access for Observatory queries
CREATE ROLE phlo_api_reader;
GRANT SELECT ON iceberg.bronze.* TO ROLE phlo_api_reader;
GRANT SELECT ON iceberg.silver.* TO ROLE phlo_api_reader;
GRANT SELECT ON iceberg.gold.* TO ROLE phlo_api_reader;

-- dagster: read/write access for workflow execution
CREATE ROLE phlo_dagster_writer;
GRANT SELECT ON iceberg.*.* TO ROLE phlo_dagster_writer;
GRANT INSERT ON iceberg.*.* TO ROLE phlo_dagster_writer;
GRANT CREATE TABLE ON SCHEMA iceberg.bronze TO ROLE phlo_dagster_writer;
GRANT CREATE TABLE ON SCHEMA iceberg.silver TO ROLE phlo_dagster_writer;
GRANT CREATE TABLE ON SCHEMA iceberg.gold TO ROLE phlo_dagster_writer;
GRANT CREATE TABLE ON SCHEMA iceberg.stage TO ROLE phlo_dagster_writer;


-- ============================================================================
-- PostgreSQL roles
-- ============================================================================
-- Run against the phlo metadata database as superuser

-- Passwords below are placeholders; rotate them before real use.
-- Note that ALTER DEFAULT PRIVILEGES affects only objects created later by
-- the role running this script (the migration superuser), not objects created
-- by these service roles themselves.

-- phlo-api service role
CREATE ROLE phlo_api_service LOGIN PASSWORD 'changeme_api';
GRANT CONNECT ON DATABASE phlo TO phlo_api_service;
GRANT USAGE ON SCHEMA public TO phlo_api_service;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO phlo_api_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO phlo_api_service;

-- dagster service role
CREATE ROLE phlo_dagster_service LOGIN PASSWORD 'changeme_dagster';
GRANT CONNECT ON DATABASE phlo TO phlo_dagster_service;
GRANT USAGE ON SCHEMA public TO phlo_dagster_service;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO phlo_dagster_service;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO phlo_dagster_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL PRIVILEGES ON TABLES TO phlo_dagster_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL PRIVILEGES ON SEQUENCES TO phlo_dagster_service;

-- hasura read-only role (for regulated mode write restriction)
CREATE ROLE phlo_hasura_readonly LOGIN PASSWORD 'changeme_hasura';
GRANT CONNECT ON DATABASE phlo TO phlo_hasura_readonly;
GRANT USAGE ON SCHEMA public TO phlo_hasura_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO phlo_hasura_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO phlo_hasura_readonly;

-- postgrest read-only role (for regulated mode write restriction)
CREATE ROLE phlo_postgrest_readonly LOGIN PASSWORD 'changeme_postgrest';
GRANT CONNECT ON DATABASE phlo TO phlo_postgrest_readonly;
GRANT USAGE ON SCHEMA public TO phlo_postgrest_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO phlo_postgrest_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO phlo_postgrest_readonly;
