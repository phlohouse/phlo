-- SQLite run-evidence schema version 2, mirroring 003_reconcile_run_evidence.sql.
ALTER TABLE pipeline_run ADD COLUMN last_heartbeat_at TEXT;
ALTER TABLE pipeline_run ADD COLUMN reconciled_at TEXT;
ALTER TABLE pipeline_run ADD COLUMN reconciliation_reason TEXT;
ALTER TABLE run_event ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE run_resource ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE run_catalog_change ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE run_quality_result ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE run_artifact ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
-- SQLite cannot drop a column's NOT NULL constraint in place, so pipeline_run
-- is rebuilt as pipeline_run_v2 with started_at made nullable, then swapped
-- in under the original name.
CREATE TABLE pipeline_run_v2 (
    project_id TEXT NOT NULL, run_id TEXT NOT NULL, pipeline_name TEXT,
    provider_run_id TEXT, trigger TEXT, initiator TEXT, effective_identity TEXT,
    partition_key TEXT, code_version TEXT, config_version TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0), trace_id TEXT,
    status TEXT NOT NULL, started_at TEXT, finished_at TEXT, failure_summary TEXT,
    evidence_completeness TEXT NOT NULL CHECK (
        evidence_completeness IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat_at TEXT, reconciled_at TEXT, reconciliation_reason TEXT,
    PRIMARY KEY (project_id, run_id)
);
-- SELECT * relies on pipeline_run_v2 declaring the same columns, in the same
-- order, as the table being replaced.
INSERT INTO pipeline_run_v2 SELECT * FROM pipeline_run;
DROP TABLE pipeline_run;
ALTER TABLE pipeline_run_v2 RENAME TO pipeline_run;
CREATE TABLE IF NOT EXISTS run_reconciliation_decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_completeness TEXT NOT NULL CHECK (
        evidence_completeness IN ('complete', 'incomplete', 'missing', 'expired', 'redacted')
    ),
    reason TEXT NOT NULL,
    missing_evidence TEXT NOT NULL DEFAULT '[]',
    evidence_checksum TEXT NOT NULL,
    observed_event_count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    heartbeat_at TEXT,
    stale_after_seconds INTEGER,
    decided_at TEXT NOT NULL,
    finished_at TEXT,
    record_checksum TEXT NOT NULL,
    UNIQUE (project_id, decision_id),
    FOREIGN KEY (project_id, run_id) REFERENCES pipeline_run(project_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_project_heartbeat
    ON pipeline_run(project_id, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_project_started
    ON pipeline_run(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_reconciliation_project_run
    ON run_reconciliation_decision(project_id, run_id, attempt, decided_at);
CREATE INDEX IF NOT EXISTS idx_run_resource_project_run_attempt
    ON run_resource(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_catalog_change_project_run_attempt
    ON run_catalog_change(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_quality_project_run_attempt
    ON run_quality_result(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_artifact_project_run_attempt
    ON run_artifact(project_id, run_id, attempt);
CREATE TRIGGER IF NOT EXISTS trg_run_event_attempt_positive_insert
    BEFORE INSERT ON run_event
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_event attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_event_attempt_positive_update
    BEFORE UPDATE OF attempt ON run_event
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_event attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_resource_attempt_positive_insert
    BEFORE INSERT ON run_resource
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_resource attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_resource_attempt_positive_update
    BEFORE UPDATE OF attempt ON run_resource
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_resource attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_catalog_change_attempt_positive_insert
    BEFORE INSERT ON run_catalog_change
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_catalog_change attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_catalog_change_attempt_positive_update
    BEFORE UPDATE OF attempt ON run_catalog_change
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_catalog_change attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_quality_result_attempt_positive_insert
    BEFORE INSERT ON run_quality_result
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_quality_result attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_quality_result_attempt_positive_update
    BEFORE UPDATE OF attempt ON run_quality_result
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_quality_result attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_artifact_attempt_positive_insert
    BEFORE INSERT ON run_artifact
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_artifact attempt must be positive'); END;
CREATE TRIGGER IF NOT EXISTS trg_run_artifact_attempt_positive_update
    BEFORE UPDATE OF attempt ON run_artifact
    WHEN NEW.attempt <= 0
    BEGIN SELECT RAISE(ABORT, 'run_artifact attempt must be positive'); END;
INSERT OR IGNORE INTO run_evidence_schema_version(version) VALUES (2);
