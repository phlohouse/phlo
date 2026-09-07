# Troubleshooting Guide

## Debugging Common Issues in Phlo

This guide helps you debug and fix common problems in Phlo.

---

## Start With `phlo doctor`

Run:

```bash
phlo doctor
```

If the output reports plugin discovery failures, rerun:

```bash
phlo doctor --verbose
```

Attach `phlo doctor --json` output when reporting setup issues.

---

## Table of Contents

1. [Services Won't Start](#services-wont-start)
2. [Dagster Issues](#dagster-issues)
3. [dbt Issues](#dbt-issues)
4. [Trino/Query Issues](#trinoquery-issues)
5. [Iceberg/Nessie Issues](#icebergnessie-issues)
6. [Data Quality Issues](#data-quality-issues)
7. [Performance Issues](#performance-issues)
8. [Debugging Techniques](#debugging-techniques)

---

## Services Won't Start

### Experimental Podman backend unavailable

Podman is a documented experimental backend and is not CI-supported. Docker Compose v2
is the currently supported path.

Run:

```bash
podman info
podman machine list
podman compose version
```

If the machine is stopped:

```bash
podman machine start
```

If `podman compose version` fails, install or configure a Compose provider such as
`podman-compose` or `docker-compose`, then rerun the Phlo command with:

```bash
PHLO_CONTAINER_BACKEND=podman phlo services status
```

### Docker Compose Fails

**Symptom:** `docker-compose up` fails or services crash

**Check 1: Port conflicts**

```bash
# See what's using ports
lsof -i :3000  # Dagster
lsof -i :10011 # Trino
lsof -i :9000  # MinIO

# Kill conflicting process
kill -9 <PID>
```

**Check 2: Insufficient resources**

```bash
# Check Docker resources
docker stats

# Increase in Docker Desktop:
# Settings → Resources → increase CPU/Memory
```

**Check 3: Check logs**

```bash
# View logs for specific service
docker-compose logs postgres
docker-compose logs dagster-webserver
docker-compose logs trino

# Follow logs in real-time
docker-compose logs -f dagster-webserver
```

**Check 4: Clean start**

```bash
# Stop everything
docker-compose down

# Remove volumes (CAUTION: deletes data!)
docker-compose down -v

# Rebuild and start
docker-compose up --build
```

### Postgres Won't Start

**Symptom:** `connection refused` or postgres container crashes

**Solution 1: Check data directory permissions**

```bash
# Ensure postgres data directory writable
sudo chown -R 999:999 ./postgres-data

# Or remove and recreate
docker-compose down
rm -rf ./postgres-data
docker-compose up postgres
```

**Solution 2: Check for corruption**

```bash
# View postgres logs
docker-compose logs postgres

# If corrupted, remove data
docker-compose down
docker volume rm cascade_postgres-data
docker-compose up postgres
```

### MinIO Won't Start

**Symptom:** `cannot write to data directory`

**Solution:**

```bash
# Fix permissions
sudo chown -R 1000:1000 ./minio-data

# Or recreate
docker-compose down
rm -rf ./minio-data
docker-compose up minio
```

### Trino Won't Start

**Symptom:** Trino crashes on startup

**Check 1: Memory**

```bash
# Trino needs at least 2GB
docker stats cascade_trino

# Increase in docker-compose.yml:
trino:
  deploy:
    resources:
      limits:
        memory: 4G
```

**Check 2: Nessie connection**

```bash
# Ensure Nessie is running
docker-compose ps nessie

# Check Nessie is healthy
curl http://localhost:10003/api/v1/trees
```

**Check 3: Configuration**

```bash
# View Trino logs
docker-compose logs trino | grep ERROR
```

---

## Dagster Issues

### Assets Not Showing in UI

**Solution 1: Restart services**

```bash
docker-compose restart dagster-webserver dagster-daemon
```

**Solution 2: Check asset registration**

```python
# Your assets should be discoverable via phlo_dagster.framework.definitions
# (assets registered in workflows/ via decorators and plugins)
from phlo_dagster.framework.definitions import defs
```

**Solution 3: Check for syntax errors**

```bash
# Test definitions load
docker-compose exec dagster-webserver python -c "from phlo_dagster.framework.definitions import defs; print(defs)"
```

### Asset Materialization Fails

**Debug steps:**

**1. Check logs in UI**

- Go to Runs → Find failed run → Click run → View logs
- Look for error message at bottom

**2. Check dependencies**

```bash
# Ensure upstream assets materialized
dagster asset materialize -m phlo_dagster.framework.definitions -a upstream_asset
```

**3. Test locally**

```python
# In Python shell
from phlo_dagster.framework.definitions import defs
from dagster import materialize

asset_def = defs.get_asset_def("my_asset")
result = materialize([asset_def])
print(result)
```

**4. Check resources**

```python
# Are resources configured?
from phlo_trino.settings import get_settings
config = get_settings()
print(config.trino_host)  # Should print value, not error
```

### Dagster Daemon Not Running

**Symptom:** Schedules/sensors not triggering

**Solution:**

```bash
# Check daemon status
docker-compose ps dagster-daemon

# Restart daemon
docker-compose restart dagster-daemon

# View daemon logs
docker-compose logs dagster-daemon
```

### Slow UI

**Solution 1: Clear instance cache**

```bash
docker-compose exec dagster-webserver rm -rf /tmp/dagster-*
docker-compose restart dagster-webserver
```

**Solution 2: Increase resources**

```yaml
# docker-compose.yml
dagster-webserver:
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 2G
```

---

## dbt Issues

### dbt Models Won't Run

**Symptom:** `dbt run` fails

**Check 1: Connection**

```bash
# Test Trino connection
docker-compose exec dagster-webserver \
  dbt debug --project-dir /app/workflows/transforms/dbt

# Should show all checks passing
```

**Check 2: Syntax errors**

```bash
# Compile first (catches syntax errors)
docker-compose exec dagster-webserver \
  dbt compile --project-dir /app/workflows/transforms/dbt
```

**Check 3: Dependencies**

```bash
# Check source exists
docker-compose exec trino trino --execute \
  "SHOW TABLES IN iceberg.raw"

# Should list your source tables
```

### Compilation Errors

**Symptom:** `Compilation Error in model my_model`

**Debug:**

```bash
# View compiled SQL
docker-compose exec dagster-webserver \
  dbt compile --project-dir /app/workflows/transforms/dbt

# Check: workflows/transforms/dbt/target/compiled/phlo/models/your_model.sql
# This shows the actual SQL generated
```

**Common issues:**

**1. Undefined variable**

```sql
-- Error: {{ ref('typo_table_name') }}
-- Fix: {{ ref('correct_table_name') }}
```

**2. Missing source**

```sql
-- Error: {{ source('raw', 'nonexistent') }}
-- Fix: Define source in sources.yml first
```

**3. SQL syntax error**

```sql
-- Error: Missing comma
SELECT
    col1
    col2  -- Missing comma!
FROM table

-- Fix: Add comma
SELECT
    col1,
    col2
FROM table
```

### dbt Tests Fail

**Debug failing test:**

```bash
# Run single test
docker-compose exec dagster-webserver \
  dbt test --project-dir /app/workflows/transforms/dbt \
  --select fct_orders,column:customer_id,test_name:not_null

# View failed rows
docker-compose exec trino trino --execute \
  "SELECT * FROM iceberg.silver.fct_orders WHERE customer_id IS NULL"
```

**Common test failures:**

**1. not_null fails**

```sql
-- Find NULL values
SELECT * FROM table WHERE column_name IS NULL

-- Fix in model:
SELECT
    COALESCE(column_name, 'UNKNOWN') AS column_name
FROM source
```

**2. unique fails**

```sql
-- Find duplicates
SELECT
    column_name,
    COUNT(*) as count
FROM table
GROUP BY column_name
HAVING COUNT(*) > 1

-- Fix: Add DISTINCT or GROUP BY
SELECT DISTINCT column_name
FROM source
```

**3. relationships fails**

```sql
-- Find orphaned records
SELECT o.*
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL

-- Fix: Add join or filter
WHERE customer_id IN (SELECT customer_id FROM customers)
```

### Incremental Model Issues

**Symptom:** Incremental model not updating

**Check 1: is_incremental() logic**

```sql
SELECT * FROM source

{% if is_incremental() %}
    -- Is this condition correct?
    WHERE updated_at > (SELECT MAX(updated_at) FROM {{ this }})
{% endif %}
```

**Check 2: Force full refresh**

```bash
# Rebuild from scratch
dbt run --select my_incremental_model --full-refresh
```

**Check 3: Check unique_key**

```sql
{{ config(
    materialized='incremental',
    unique_key='id',  # Does this column exist and is it unique?
) }}
```

---

## Trino/Query Issues

### Query Fails

**Symptom:** `Query failed: ...`

**Common errors:**

**1. Table not found**

```
Error: Table 'iceberg.raw.my_table' does not exist

Solutions:
- Check table actually exists: SHOW TABLES IN iceberg.raw
- Check spelling/capitalization
- Ensure asset materialized first
```

**2. Column not found**

```
Error: Column 'colum_name' cannot be resolved

Solutions:
- Check column exists: DESCRIBE iceberg.raw.my_table
- Check for typo
- Ensure using correct table version
```

**3. Type mismatch**

```
Error: Cannot cast VARCHAR to INTEGER

Solutions:
- Use explicit CAST: CAST(column AS INTEGER)
- Handle NULLs: CAST(NULLIF(column, '') AS INTEGER)
- Use TRY_CAST: TRY_CAST(column AS INTEGER)
```

**4. Out of memory**

```
Error: Query exceeded per-node user memory limit

Solutions:
- Add LIMIT to query
- Filter data earlier: WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
- Increase Trino memory in docker-compose.yml
```

### Slow Queries

**Debug:**

```sql
-- Check query plan
EXPLAIN SELECT * FROM large_table

-- Check table stats
SHOW STATS FOR iceberg.silver.fct_orders
```

**Optimizations:**

**1. Add filters**

```sql
-- Bad: Full scan
SELECT * FROM fct_orders

-- Good: Filtered
SELECT * FROM fct_orders
WHERE order_date >= CURRENT_DATE - INTERVAL '30' DAY
```

**2. Limit results**

```sql
-- Add LIMIT for exploration
SELECT * FROM fct_orders LIMIT 1000
```

**3. Use columnar format (Parquet)**

- Iceberg already uses Parquet ✅

**4. Partition data**

```python
# Partition Iceberg table by date
schema = Schema(
    ...,
    partition_spec=PartitionSpec(
        PartitionField("order_date", "day")
    )
)
```

### Connection Issues

**Symptom:** `Connection refused` or `Timeout`

**Solution:**

```bash
# Check Trino is running
docker-compose ps trino

# Check Trino is healthy
curl http://localhost:10011/v1/info

# Check firewall isn't blocking
telnet localhost 10011

# Restart Trino
docker-compose restart trino
```

---

## Iceberg/Nessie Issues

### Table Not Found

**Symptom:** `Table 'iceberg.raw.my_table' does not exist`

**Check 1: List tables**

```bash
# Connect to Trino
docker-compose exec trino trino

# List schemas
SHOW SCHEMAS IN iceberg;

# List tables in schema
SHOW TABLES IN iceberg.raw;
```

**Check 2: Check branch**

```bash
# Are you on the right branch?
# To query dev branch, use iceberg_dev catalog
SELECT * FROM iceberg_dev.raw.my_table
```

**Check 3: Ensure asset materialized**

```bash
# Materialize the ingestion asset first
dagster asset materialize -m phlo_dagster.framework.definitions -a my_ingestion_asset
```

### Nessie API Errors

**Symptom:** `Failed to connect to Nessie`

**Solution:**

```bash
# Check Nessie is running
docker-compose ps nessie

# Test Nessie API
curl http://localhost:10003/api/v1/trees

# Should return: {"name": "main", ...}

# Restart Nessie
docker-compose restart nessie
```

### WAP promotion has an unknown merge outcome

**Symptom:** `.phlo/wap-reports/<logical-run-id>.json` records
`status: promotion_pending`, `merge_state: merge_started`, and
`failure_reason: merge_outcome_unknown`.

The catalog merge may have completed before the worker lost its acknowledgement
or failed to persist its receipt. A changed main hash does not prove that this
run was published: another writer could have advanced main. Phlo therefore
retains the source branch, skips automatic merge replay, and exempts this
unresolved intent from retention cleanup.

Preserve the report and source branch. Inspect catalog history and the exact
source content/snapshots against the target to establish whether this batch was
published. Do not mark the report merged or delete the branch merely because
main moved. If the outcome cannot be proved, keep the branch for recovery;
reingesting the batch could duplicate data if the original merge succeeded.
There is currently no automatic receipt reconstruction or operator resolution
command for this state. A verified catalog outcome must be reconciled before
resuming promotion. A durable `merge_state: merged` receipt already allows
normal evidence and cleanup retries without repeating the catalog merge.

An explicitly rejected merge is recorded as `merge_state: merge_failed` and
remains eligible for a later merge attempt; its source follows the normal
failed-run retention policy.

### Branch Issues

**List branches:**

```bash
curl http://localhost:10003/api/v1/trees
```

**Create branch:**

```bash
curl -X POST http://localhost:10003/api/v1/trees/branch/dev \
  -H "Content-Type: application/json" \
  -d '{"sourceRefName": "main"}'
```

**Switch branch in Trino:**

```sql
-- Use iceberg catalog (main branch)
SELECT * FROM iceberg.raw.my_table;

-- Use iceberg_dev catalog (dev branch)
SELECT * FROM iceberg_dev.raw.my_table;
```

---

## Data Quality Issues

### Missing Data

**Debug steps:**

**1. Check source**

```sql
-- Does raw data exist?
SELECT COUNT(*), MIN(date), MAX(date)
FROM iceberg.raw.my_source_table
```

**2. Check transformations**

```sql
-- Are rows being filtered out?
-- Check each layer
SELECT COUNT(*) FROM iceberg.raw.my_table;          -- 1000 rows
SELECT COUNT(*) FROM iceberg.bronze.stg_my_table;   -- 950 rows (where did 50 go?)
SELECT COUNT(*) FROM iceberg.silver.fct_my_table;   -- 900 rows (where did 50 go?)
```

**3. Check for filters**

```sql
-- Review model SQL
-- Look for WHERE clauses that might be too aggressive
WHERE date >= '2024-01-01'  -- Are you excluding earlier data?
  AND status = 'active'      -- Are you excluding inactive records?
```

### Duplicate Data

**Debug:**

```sql
-- Find duplicates
SELECT
    id,
    COUNT(*) as count
FROM iceberg.silver.fct_my_table
GROUP BY id
HAVING COUNT(*) > 1
```

**Solutions:**

**1. Add DISTINCT**

```sql
SELECT DISTINCT * FROM source
```

**2. Use GROUP BY**

```sql
SELECT
    id,
    MAX(updated_at) AS updated_at,
    ...
FROM source
GROUP BY id
```

**3. Use ROW_NUMBER()**

```sql
WITH ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY id
            ORDER BY updated_at DESC
        ) AS rn
    FROM source
)
SELECT * FROM ranked WHERE rn = 1
```

### Incorrect Values

**Debug:**

```sql
-- Check value distributions
SELECT
    column_name,
    COUNT(*) as count,
    MIN(value) as min_val,
    MAX(value) as max_val,
    AVG(value) as avg_val
FROM table
GROUP BY column_name
```

**Check transformations:**

```sql
-- Review calculated fields
-- Is the formula correct?
amount * 1.1 AS total  -- Should this be amount * 1.1 or amount + (amount * 0.1)?
```

---

## Performance Issues

### Slow Asset Materialization

**Debug:**

**1. Check logs for bottlenecks**

```bash
# Find slow steps
docker-compose logs dagster-webserver | grep "Execution time"
```

**2. Profile query**

```sql
-- In Trino, check query runtime
EXPLAIN ANALYZE SELECT * FROM expensive_query
```

**Solutions:**

**1. Add partitions**

```python
@dg.asset(partitions_def=daily_partition)
def partitioned_asset(context):
    date = context.partition_key
    # Process only one day
```

**2. Use incremental dbt models**

```sql
{{ config(materialized='incremental') }}
```

**3. Optimize queries**

```sql
-- Bad: Full table scan
SELECT * FROM large_table

-- Good: Filtered and limited
SELECT *
FROM large_table
WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
LIMIT 10000
```

**4. Increase resources**

```yaml
# docker-compose.yml
dagster-webserver:
  deploy:
    resources:
      limits:
        cpus: "4"
        memory: 8G
```

### Slow Dashboard

**Solutions:**

**1. Pre-aggregate data**

```sql
-- Instead of aggregating in dashboard,
-- pre-aggregate in gold layer
SELECT
    date,
    SUM(amount) as total_amount,
    COUNT(*) as total_orders
FROM fct_orders
GROUP BY date
```

**2. Publish to PostgreSQL**

- Marts published to PostgreSQL are much faster than querying Iceberg

**3. Add indexes** (in PostgreSQL marts)

```sql
CREATE INDEX idx_mrt_orders_date ON marts.mrt_orders(order_date);
```

---

## Debugging Techniques

### Enable Verbose Logging

**Dagster:**

```python
@dg.asset
def my_asset(context: dg.AssetExecutionContext):
    context.log.set_level(logging.DEBUG)
    context.log.debug("Detailed debug info")
```

**dbt:**

```bash
dbt run --debug
```

**Trino:**

```bash
# Enable query logging
docker-compose exec trino trino --debug
```

### Inspect Data at Each Step

```sql
-- Check raw
SELECT * FROM iceberg.raw.my_table LIMIT 10;

-- Check bronze
SELECT * FROM iceberg.bronze.stg_my_table LIMIT 10;

-- Check silver
SELECT * FROM iceberg.silver.fct_my_table LIMIT 10;

-- Compare counts
SELECT 'raw' as layer, COUNT(*) FROM iceberg.raw.my_table
UNION ALL
SELECT 'bronze', COUNT(*) FROM iceberg.bronze.stg_my_table
UNION ALL
SELECT 'silver', COUNT(*) FROM iceberg.silver.fct_my_table;
```

### Use Dagster Playground

Test assets interactively:

1. Open Dagster UI
2. Assets → Click asset → "Launchpad" tab
3. Modify config
4. Click "Materialize"

### Query Compiled SQL

For dbt issues:

```bash
# Compile models
dbt compile --project-dir /app/workflows/transforms/dbt

# View compiled SQL
cat workflows/transforms/dbt/target/compiled/phlo/models/silver/fct_my_model.sql

# Run compiled SQL directly in Trino to debug
docker-compose exec trino trino < compiled.sql
```

### Check Metadata Tables

**Iceberg metadata:**

```sql
-- View snapshots
SELECT * FROM iceberg.raw."my_table$snapshots";

-- View manifests
SELECT * FROM iceberg.raw."my_table$manifests";

-- View files
SELECT * FROM iceberg.raw."my_table$files";
```

**Nessie metadata:**

```bash
# View commit log
curl http://localhost:10003/api/v1/trees/branch/main/log

# View specific table history
curl http://localhost:10003/api/v1/trees/branch/main/contents/raw.my_table
```

### Isolate the Problem

**Binary search:**

1. Does the source data exist? → Yes
2. Does bronze model work? → Yes
3. Does silver model work? → No ← Problem is here

**Test in isolation:**

```sql
-- Run transformation logic manually
SELECT
    id,
    -- Your transformation
    CASE WHEN value < 0 THEN 0 ELSE value END AS clean_value
FROM iceberg.bronze.stg_my_table
LIMIT 10
```

---

## Getting Help

### Check Logs

Always check logs first:

```bash
# All logs
docker-compose logs

# Specific service
docker-compose logs dagster-webserver
docker-compose logs trino
docker-compose logs postgres

# Follow logs
docker-compose logs -f dagster-webserver

# Last 100 lines
docker-compose logs --tail=100 dagster-webserver
```

### Search Documentation

- **Dagster Docs:** https://docs.dagster.io
- **dbt Docs:** https://docs.getdbt.com
- **Trino Docs:** https://trino.io/docs
- **Iceberg Docs:** https://iceberg.apache.org/docs
- **Nessie Docs:** https://projectnessie.org/docs

### Ask for Help

When asking for help, include:

1. **What you're trying to do**
2. **What you expected to happen**
3. **What actually happened**
4. **Error messages** (full stack trace)
5. **Logs** (relevant portions)
6. **Code** (the asset/model causing issues)
7. **What you've tried** (troubleshooting steps)

---

## Summary

**Debugging Process:**

1. **Read the error message** carefully
2. **Check logs** for details
3. **Isolate the problem** (which component?)
4. **Test in isolation** (can you reproduce?)
5. **Check dependencies** (are upstream steps ok?)
6. **Verify configuration** (is everything configured?)
7. **Search documentation** (is this a known issue?)
8. **Ask for help** (provide details!)

**Common Solutions:**

- Restart services
- Check logs
- Verify configuration
- Test connections
- Check dependencies
- Increase resources
- Clear caches

**Prevention:**

- ✅ Add tests
- ✅ Add logging
- ✅ Add error handling
- ✅ Monitor performance
- ✅ Document assumptions
- ✅ Version control everything

---

**Next:** [Best Practices Guide](best-practices.md) - Build better pipelines.
