-- Personal read receipts do not alter shared incident activity or subscriptions.
CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_event_environment_identity
    ON phlo.incident_event (env, event_id);
CREATE TABLE IF NOT EXISTS phlo.incident_notification_read (
    env text NOT NULL,
    subject text NOT NULL,
    event_id text NOT NULL,
    read_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (env, subject, event_id),
    FOREIGN KEY (env, event_id) REFERENCES phlo.incident_event (env, event_id)
);
