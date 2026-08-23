-- Run-evidence schema version 2: heartbeat-based reconciliation.
-- started_at becomes nullable: from this version a run row may exist without
-- an observed start event. attempt columns are backfilled with a default of 1.
ALTER TABLE phlo.pipeline_run ALTER COLUMN started_at DROP NOT NULL;
ALTER TABLE phlo.pipeline_run ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
ALTER TABLE phlo.pipeline_run ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;
ALTER TABLE phlo.pipeline_run ADD COLUMN IF NOT EXISTS reconciliation_reason TEXT;
ALTER TABLE phlo.run_event ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE phlo.run_resource ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE phlo.run_catalog_change ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE phlo.run_quality_result ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);
ALTER TABLE phlo.run_artifact ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0);

CREATE TABLE IF NOT EXISTS phlo.run_reconciliation_decision (
    id BIGSERIAL PRIMARY KEY,
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
    missing_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_checksum TEXT NOT NULL,
    observed_event_count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    heartbeat_at TIMESTAMPTZ,
    stale_after_seconds INTEGER,
    decided_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    record_checksum TEXT NOT NULL,
    UNIQUE (project_id, decision_id),
    FOREIGN KEY (project_id, run_id) REFERENCES phlo.pipeline_run(project_id, run_id)
);

INSERT INTO phlo.run_evidence_schema_version(version) VALUES (2)
ON CONFLICT (version) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_pipeline_run_project_heartbeat
    ON phlo.pipeline_run(project_id, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_run_reconciliation_project_run
    ON phlo.run_reconciliation_decision(project_id, run_id, attempt, decided_at);
CREATE INDEX IF NOT EXISTS idx_run_resource_project_run_attempt
    ON phlo.run_resource(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_catalog_change_project_run_attempt
    ON phlo.run_catalog_change(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_quality_project_run_attempt
    ON phlo.run_quality_result(project_id, run_id, attempt);
CREATE INDEX IF NOT EXISTS idx_run_artifact_project_run_attempt
    ON phlo.run_artifact(project_id, run_id, attempt);
