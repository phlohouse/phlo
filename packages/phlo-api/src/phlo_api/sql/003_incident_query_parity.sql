-- Additive incident fields, authoritative query evidence and durable delivery attempts.
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS severity text NOT NULL DEFAULT 'medium'
    CHECK (severity IN ('low', 'medium', 'high'));
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS asset_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS notify_qa boolean NOT NULL DEFAULT false;
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS pause_downstream boolean NOT NULL DEFAULT false;
ALTER TABLE phlo.incident ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'signal'
    CHECK (origin IN ('signal', 'manual'));
ALTER TABLE phlo.incident DROP CONSTRAINT IF EXISTS incident_env_asset_id_kind_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_signal_group
    ON phlo.incident (env, asset_id, kind) WHERE origin = 'signal';
CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_environment_identity
    ON phlo.incident (env, incident_id);

CREATE TABLE IF NOT EXISTS phlo.query_execution (
    query_id text PRIMARY KEY,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    actor text NOT NULL,
    nessie_ref text NOT NULL,
    engine text NOT NULL,
    statement text NOT NULL,
    statement_sha256 text NOT NULL,
    executed_statement text NOT NULL,
    result jsonb NOT NULL,
    result_sha256 text NOT NULL,
    provider_query_id text,
    completed_at timestamptz NOT NULL,
    UNIQUE (env, query_id)
);

CREATE TABLE IF NOT EXISTS phlo.incident_query_evidence (
    env text NOT NULL,
    incident_id text NOT NULL,
    query_id text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (env, incident_id, query_id),
    FOREIGN KEY (env, incident_id) REFERENCES phlo.incident (env, incident_id),
    FOREIGN KEY (env, query_id) REFERENCES phlo.query_execution (env, query_id)
);

CREATE TABLE IF NOT EXISTS phlo.incident_effect (
    effect_id text PRIMARY KEY,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    incident_id text NOT NULL,
    incident_version integer NOT NULL,
    actor text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('notification', 'pause')),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivering', 'delivered', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (env, incident_id, incident_version, kind),
    FOREIGN KEY (env, incident_id) REFERENCES phlo.incident (env, incident_id)
);
