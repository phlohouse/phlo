-- Run-evidence schema version 1. PostgreSQL is the production dialect; the
-- *_sqlite.sql sibling mirrors it for local SQLite stores. Migrations are
-- applied in numeric order by phlo.run_evidence.store, and every statement is
-- idempotent so a partially applied version can be retried.
CREATE SCHEMA IF NOT EXISTS phlo;

CREATE TABLE IF NOT EXISTS phlo.run_evidence_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per run. run_id is unique within a project, not globally.
CREATE TABLE IF NOT EXISTS phlo.pipeline_run (
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
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    failure_summary TEXT,
    evidence_completeness TEXT NOT NULL CHECK (
        evidence_completeness IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_event (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    producer TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    sequence BIGINT,
    payload JSONB NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Makes event ingestion idempotent: a producer may redeliver an event
    -- without creating duplicates.
    UNIQUE (project_id, producer, event_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_stage (
    project_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_type TEXT NOT NULL,
    provider TEXT,
    tool TEXT,
    asset TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, stage_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_resource (
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
    staged_objects JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot_before TEXT,
    snapshot_after TEXT,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, resource_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_lineage_edge (
    project_id TEXT NOT NULL,
    lineage_edge_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    column_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    origin TEXT NOT NULL,
    derivation TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, lineage_edge_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_catalog_change (
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
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, catalog_change_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_artifact (
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    uri TEXT,
    content_type TEXT,
    checksum TEXT,
    retention_class TEXT,
    expires_at TIMESTAMPTZ,
    legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL CHECK (status IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')),
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, artifact_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

CREATE TABLE IF NOT EXISTS phlo.run_quality_result (
    project_id TEXT NOT NULL,
    quality_result_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    check_id TEXT NOT NULL,
    asset TEXT,
    severity TEXT,
    blocking BOOLEAN NOT NULL DEFAULT FALSE,
    passed BOOLEAN NOT NULL,
    evaluated_count BIGINT,
    failed_count BIGINT,
    failure_artifact_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    record_checksum TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id, quality_result_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id),
    -- Deferred so a quality result can be written before the stage or failure
    -- artifact rows it references, within the same transaction.
    FOREIGN KEY (project_id, run_id, stage_id)
        REFERENCES phlo.run_stage(project_id, run_id, stage_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (project_id, run_id, failure_artifact_id)
        REFERENCES phlo.run_artifact(project_id, run_id, artifact_id)
        DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO phlo.run_evidence_schema_version(version) VALUES (1)
ON CONFLICT (version) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_pipeline_run_project_started
    ON phlo.pipeline_run(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_event_project_run_observed
    ON phlo.run_event(project_id, run_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_run_stage_project_run
    ON phlo.run_stage(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_resource_project_run
    ON phlo.run_resource(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_lineage_project_run
    ON phlo.run_lineage_edge(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_quality_project_run
    ON phlo.run_quality_result(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_catalog_project_run
    ON phlo.run_catalog_change(project_id, run_id);
CREATE INDEX IF NOT EXISTS idx_run_artifact_project_run
    ON phlo.run_artifact(project_id, run_id);
