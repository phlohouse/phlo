-- Run-evidence schema version 5: stable activity-keyset pagination.
CREATE INDEX IF NOT EXISTS idx_pipeline_run_activity_keyset
    ON phlo.pipeline_run (
        (COALESCE(finished_at, started_at, created_at)) DESC,
        project_id ASC,
        run_id ASC
    );

INSERT INTO phlo.run_evidence_schema_version(version) VALUES (5)
ON CONFLICT (version) DO NOTHING;
