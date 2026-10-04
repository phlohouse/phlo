-- Phase 3 observed Trino table reads, stored in the installation's Phlo database.
CREATE SCHEMA IF NOT EXISTS phlo;

CREATE TABLE IF NOT EXISTS phlo.query_usage_event (
    source_id text NOT NULL,
    query_id text NOT NULL,
    event_digest text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, query_id)
);

CREATE TABLE IF NOT EXISTS phlo.asset_query_usage (
    source_id text NOT NULL,
    query_id text NOT NULL,
    catalog_version text NOT NULL,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    nessie_ref text NOT NULL,
    table_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    query_state text NOT NULL CHECK (query_state IN ('FINISHED', 'FAILED')),
    PRIMARY KEY (source_id, query_id, catalog_version, env, table_id),
    FOREIGN KEY (source_id, query_id) REFERENCES phlo.query_usage_event (source_id, query_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_query_usage_page
    ON phlo.asset_query_usage (env, nessie_ref, table_id, occurred_at DESC, query_id DESC, source_id DESC, catalog_version DESC);
