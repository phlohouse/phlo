-- Unified API phase 2: durable incident, activity and asset policy state.
CREATE SCHEMA IF NOT EXISTS phlo;

CREATE TABLE IF NOT EXISTS phlo.incident (
    incident_id text PRIMARY KEY,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    asset_id text NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    status text NOT NULL CHECK (status IN ('open', 'acknowledged', 'resolved')),
    owner text,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (env, asset_id, kind)
);

CREATE TABLE IF NOT EXISTS phlo.incident_event (
    event_id text PRIMARY KEY,
    incident_id text NOT NULL REFERENCES phlo.incident,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    actor text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS phlo.incident_signal (
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    evidence_id text NOT NULL,
    incident_id text NOT NULL REFERENCES phlo.incident,
    payload_sha256 text NOT NULL,
    PRIMARY KEY (env, evidence_id)
);

CREATE TABLE IF NOT EXISTS phlo.incident_command (
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    actor text NOT NULL,
    action_target text NOT NULL,
    key text NOT NULL,
    payload_sha256 text NOT NULL,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (env, actor, action_target, key)
);

CREATE TABLE IF NOT EXISTS phlo.incident_subscription (
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    incident_id text NOT NULL REFERENCES phlo.incident,
    subject text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (env, incident_id, subject)
);

CREATE TABLE IF NOT EXISTS phlo.incident_follow_up (
    follow_up_id text PRIMARY KEY,
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    incident_id text NOT NULL REFERENCES phlo.incident,
    description text NOT NULL,
    due_at timestamptz,
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS phlo.asset_incident_policy (
    env text NOT NULL CHECK (env IN ('prod', 'staging')),
    asset_id text NOT NULL,
    owner text,
    freshness_sla_seconds integer CHECK (freshness_sla_seconds > 0),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    PRIMARY KEY (env, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_incident_activity_env_time
    ON phlo.incident_event (env, occurred_at DESC, event_id DESC);

CREATE INDEX IF NOT EXISTS idx_incident_list_env_updated
    ON phlo.incident (env, updated_at DESC, incident_id DESC);
