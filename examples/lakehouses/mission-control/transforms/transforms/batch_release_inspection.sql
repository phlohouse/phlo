-- Inspect synthetic release metrics on an isolated native Transform candidate.
SELECT
    batch_id,
    product,
    site,
    release_status,
    potency_mean_pct,
    results_held
FROM gold.batch_release_metrics
