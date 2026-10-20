select "event_key", count(*) as n from customer360.stg_consent_events group by "event_key" having count(*) > 1
