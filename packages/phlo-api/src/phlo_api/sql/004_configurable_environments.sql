-- Explicit installed-consumer upgrade. Run through phlo_api.migrations after backup.
-- Identity strings, primary/foreign keys and existing rows do not change.
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'incident', 'incident_event', 'incident_signal', 'incident_command',
        'incident_subscription', 'incident_follow_up', 'incident_schema_decision',
        'asset_incident_policy', 'asset_query_usage', 'query_execution', 'incident_effect'
    ] LOOP
        EXECUTE format('ALTER TABLE phlo.%I DROP CONSTRAINT IF EXISTS %I', target, target || '_env_check');
        EXECUTE format(
            'ALTER TABLE phlo.%I ADD CONSTRAINT %I CHECK (env ~ %L)',
            target, target || '_env_check', '^[a-z][a-z0-9_-]{0,62}$'
        );
    END LOOP;
END $$;
