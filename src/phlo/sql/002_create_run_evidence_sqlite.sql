-- Run-evidence schema version 1, SQLite form of 002_create_run_evidence.sql.
-- PostgreSQL remains the production dialect; this variant backs local SQLite
-- stores (PHLO_RUN_EVIDENCE_SQLITE_PATH). Applied by phlo.run_evidence.store
-- in numeric migration order.


CREATE TABLE IF NOT EXISTS run_evidence_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    pipeline_name TEXT,
    provider_run_id TEXT,
    trigger TEXT,
    initiator TEXT,
    effective_identity TEXT,
    partition_key TEXT,
    code_version TEXT,
    config_version TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    trace_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    failure_summary TEXT,
    evidence_completeness TEXT NOT NULL CHECK (
        evidence_completeness IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_event (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    producer TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sequence BIGINT,
    payload TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Makes event ingestion idempotent: a producer may redeliver an event
    -- without creating duplicates.
    UNIQUE (project_id, producer, event_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_stage (
    project_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_type TEXT NOT NULL,
    provider TEXT,
    tool TEXT,
    asset TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    metrics TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, stage_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_resource (
    project_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    role TEXT NOT NULL,
    normalized_identity TEXT,
    uri TEXT,
    table_name TEXT,
    catalog TEXT,
    ref_name TEXT,
    schema_hash TEXT,
    watermark TEXT,
    record_count BIGINT,
    byte_count BIGINT,
    staged_objects TEXT NOT NULL DEFAULT '[]',
    snapshot_before TEXT,
    snapshot_after TEXT,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, resource_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_lineage_edge (
    project_id TEXT NOT NULL,
    lineage_edge_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    column_mapping TEXT NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL,
    derivation TEXT NOT NULL,
    confidence REAL,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, lineage_edge_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_catalog_change (
    project_id TEXT NOT NULL,
    catalog_change_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    catalog_ref TEXT,
    content_key TEXT,
    operation TEXT NOT NULL,
    source_hash TEXT,
    target_hash TEXT,
    commit_hash TEXT,
    commit_message TEXT,
    merge_outcome TEXT,
    snapshot_before TEXT,
    snapshot_after TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, catalog_change_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_artifact (
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    uri TEXT,
    content_type TEXT,
    checksum TEXT,
    retention_class TEXT,
    expires_at TEXT,
    legal_hold INTEGER NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL CHECK (status IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')),
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, artifact_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS run_quality_result (
    project_id TEXT NOT NULL,
    quality_result_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    check_id TEXT NOT NULL,
    asset TEXT,
    severity TEXT,
    blocking INTEGER NOT NULL DEFAULT FALSE,
    passed INTEGER NOT NULL,
    evaluated_count BIGINT,
    failed_count BIGINT,
    failure_artifact_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, quality_result_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id),
    FOREIGN KEY (project_id, run_id, stage_id)
        REFERENCES run_stage(project_id, run_id, stage_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (project_id, run_id, failure_artifact_id)
        REFERENCES run_artifact(project_id, run_id, artifact_id)
        DEFERRABLE INITIALLY DEFERRED
);

INSERT OR IGNORE INTO run_evidence_schema_version(version) VALUES (1);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_project_started
    ON pipeline_run(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_event_project_run_observed
    ON run_event(project_id, run_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_run_stage_project_run
    ON run_stage(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_resource_project_run
    ON run_resource(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_lineage_project_run
    ON run_lineage_edge(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_quality_project_run
    ON run_quality_result(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_catalog_project_run
    ON run_catalog_change(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_artifact_project_run
    ON run_artifact(project_id, run_id);
