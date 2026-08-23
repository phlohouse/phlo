-- Schema registry for data-contract enforcement, loaded by
-- phlo.schema_registry at store setup. Each row captures one table's
-- schema at one point in time; UNIQUE (table_name, schema_hash) keeps a single
-- row per distinct shape, so history records changes, not individual runs.
CREATE SCHEMA IF NOT EXISTS phlo;

CREATE TABLE IF NOT EXISTS phlo.schema_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    schema JSONB NOT NULL,
    schema_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    run_id TEXT,
    source TEXT,
    UNIQUE (table_name, schema_hash)
);

CREATE INDEX IF NOT EXISTS idx_schema_snapshots_table_created
    ON phlo.schema_snapshots (table_name, created_at DESC);

COMMENT ON TABLE phlo.schema_snapshots IS 'Tracks schema snapshots for data contract enforcement';
