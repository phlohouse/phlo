-- Synthetic lakehouse comparison model. The observed main schema uses do_pct.
SELECT
    run_id,
    vessel_id,
    date_trunc('minute', from_iso8601_timestamp(ts)) AS minute,
    round(avg(ph), 2) AS ph,
    round(avg(do_pct), 1) AS do_pct,
    round(avg(temp_c), 2) AS temp_c,
    count(*) AS n_readings
FROM bronze.bioreactor_telemetry
GROUP BY 1, 2, 3
