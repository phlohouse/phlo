# Operations Guide

Production operations guide for running and maintaining Phlo.

## Daily Operations

### Monitoring Services

Check all services are running:

```bash
phlo services status
```

Expected output:

```
SERVICE              STATUS    PORTS
postgres             running   10000
minio                running   10001-10002
nessie               running   10003
trino                running   10005
dagster-webserver    running   10006
dagster-daemon       running
```

### Viewing Logs

Monitor service logs:

```bash
# All services
phlo services logs -f

# Specific service
phlo services logs -f dagster-webserver

# Last 100 lines
phlo services logs --tail 100 dagster-daemon
```

### Asset Status

Check asset health and freshness:

```bash
# All assets
phlo status

# Only stale assets
phlo status --stale

# Specific group
phlo status --group csv
```

### Manual Materialization

Trigger asset runs manually:

```bash
# Single asset
phlo materialize dlt_events

# With downstream
phlo materialize dlt_events+

# Specific partition
phlo materialize dlt_events --partition 2026-05-04

# By tag
phlo materialize --select "tag:csv"
```

## Backup and Recovery

### Database Backups

**PostgreSQL**:

```bash
# Backup
docker exec phlo-postgres-1 pg_dump -U postgres cascade | gzip > backup.sql.gz

# Restore
gunzip < backup.sql.gz | docker exec -i phlo-postgres-1 psql -U postgres -d cascade
```

**Automated backups**:

```bash
# Add to crontab
0 2 * * * /path/to/backup-postgres.sh
```

```bash
#!/bin/bash
# backup-postgres.sh
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/postgres"
mkdir -p $BACKUP_DIR
docker exec phlo-postgres-1 pg_dump -U postgres cascade | \
  gzip > $BACKUP_DIR/cascade_$DATE.sql.gz

# Keep only last 30 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete
```

### Object Storage Backups

**MinIO/S3**:

```bash
# Install mc (MinIO client)
brew install minio/stable/mc

# Configure
mc alias set local http://localhost:10001 minioadmin minioadmin

# Backup bucket
mc mirror local/lake /backups/minio/lake

# Restore
mc mirror /backups/minio/lake local/lake
```

### Migrating Service Data Volumes

Some Phlo service packages persist runtime data in Docker named volumes instead of
project-local `./volumes/<service>` bind mounts. Before upgrading an existing
project that already has bind-mounted data, copy the old directory contents into
the named volume once.

Run these commands from the project root, after stopping the Phlo stack:

```bash
docker volume create loki-data
docker run --rm -v "$(pwd)/volumes/loki:/source:ro" -v loki-data:/dest \
  alpine sh -c "cp -a /source/. /dest/"

docker volume create grafana-data
docker run --rm -v "$(pwd)/volumes/grafana:/source:ro" -v grafana-data:/dest \
  alpine sh -c "cp -a /source/. /dest/"

docker volume create prometheus-data
docker run --rm -v "$(pwd)/volumes/prometheus:/source:ro" -v prometheus-data:/dest \
  alpine sh -c "cp -a /source/. /dest/"

docker volume create clickstack-data
docker run --rm -v "$(pwd)/volumes/clickstack:/source:ro" -v clickstack-data:/dest \
  alpine sh -c "cp -a /source/. /dest/"

docker volume create superset-home
docker run --rm -v "$(pwd)/volumes/superset:/source:ro" -v superset-home:/dest \
  alpine sh -c "cp -a /source/. /dest/"
```

Skip any command for a service you have not used or whose source directory does
not exist.

**Automated S3 sync**:

```bash
# Using AWS CLI or rclone
rclone sync /backups/minio/lake s3://backup-bucket/lake
```

### Nessie Catalog Backups

Nessie state is stored in PostgreSQL, so backing up Postgres includes catalog metadata.

**Export specific branches**:

```bash
# List branches
phlo branch list > branches_backup.txt

# Export branch commits
curl http://localhost:10003/api/v1/trees/main > main_branch.json
```

## Branch Management

### Creating Branches

```bash
# Development branch
phlo branch create dev

# Feature branch from specific ref
phlo branch create feature-xyz --from main

# With description
phlo branch create experiment --description "Testing new ingestion"
```

### Merging Branches

```bash
# Merge dev to main
phlo branch merge dev main

# Force merge commit
phlo branch merge dev main --no-ff
```

### Cleanup Old Branches

```bash
# List all branches
phlo branch list

# Delete specific branch
phlo branch delete old-feature

# Automated cleanup (configure in .phlo/.env.local)
BRANCH_CLEANUP_ENABLED=true
BRANCH_RETENTION_DAYS=7
BRANCH_RETENTION_DAYS_FAILED=2
```

**Manual cleanup script**:

```python
from phlo_nessie.resource import BranchManagerResource
from datetime import datetime, timedelta

branch_manager = BranchManagerResource()

# Get all pipeline branches
branches = branch_manager.get_all_pipeline_branches()

retention_days = 7
cutoff_date = datetime.now() - timedelta(days=retention_days)

for branch in branches:
    if branch.created_at < cutoff_date:
        print(f"Deleting old branch: {branch.name}")
        branch_manager.cleanup_branch(branch.name)
```

## Performance Optimization

### Trino Query Optimization

**Enable query profiling**:

```sql
-- In Trino CLI
EXPLAIN ANALYZE SELECT * FROM bronze.events WHERE date = '2025-01-15';
```

**Partition pruning**:

```sql
-- Good: uses partition pruning
SELECT * FROM bronze.events WHERE partition_date = '2025-01-15';

-- Bad: full table scan
SELECT * FROM bronze.events WHERE timestamp > '2025-01-15';
```

**Table statistics**:

```sql
-- Analyze table
ANALYZE iceberg_dev.bronze.events;

-- Show stats
SHOW STATS FOR bronze.events;
```

### Iceberg Maintenance

**Optimize files**:

```python
from phlo_iceberg import IcebergResource
from phlo_trino import TrinoResource

iceberg = IcebergResource(ref="main")
trino = TrinoResource(ref="main")

# Plan without invoking Trino. The snapshot token is checked again by the
# provider immediately before OPTIMIZE.
plan = iceberg.compact(table_name="bronze.events", dry_run=True)

# Execute through the ref-aware Trino provider. This does not expire snapshots.
result = iceberg.compact(
    table_name="bronze.events",
    expected_snapshot_id=plan["before_snapshot_id"],
    operation_id="maintenance-2026-07-13-001",
    executor=trino,
)
```

The result reports `planned`, `succeeded`, `noop`, `blocked`, or `failed`, with
the observed snapshot transition and structured failure details. Compaction does
not expire snapshots, so snapshot expiration and orphan-file cleanup remain
separate operations. A stale snapshot token blocks execution, and Iceberg's
commit conflict handling rejects a concurrent commit after the provider's
best-effort head check; the check is not an atomic fence. The operation ID is
correlation evidence only: execution is at-least-once, and a provider error
after submission is `failed` with outcome-unknown evidence, so operators must
reconcile the table before retrying. The execute path must use an executor
configured for the requested ref.

**Automated maintenance**:

```python
# These Dagster jobs produce retention plans. dry_run=True is plan-only; with
# dry_run=False, snapshot expiry can submit threshold-based deletion after the
# plan checks, while orphan cleanup remains planning-only.
from phlo_dagster.iceberg_maintenance import (
    expire_snapshots_job,
    orphan_cleanup_job,
)
```

The operations behind these jobs return structured per-table evidence and emit the existing
maintenance telemetry. Dry-run planning uses Iceberg metadata and the configured
object-store listing, so it does not need a Trino executor. Snapshot expiry keeps
the seven-day floor, current-snapshot fence, and table snapshot-reference evidence
in its plan; Nessie-wide branch/tag evidence is unavailable to this capability.
With `dry_run=False`, the ref-aware executor rechecks the current snapshot and
submits Trino's threshold-only expiry procedure, so snapshots can be deleted.
That procedure cannot bind the full metadata/data deletion surface or every Nessie
reference to the plan, and it does not enforce `retain_last`; the exact deleted
snapshot set is unavailable and submission is non-atomic. If submission fails,
the outcome may be unknown and the table must be reconciled before retrying.
Orphan discovery is supported in dry-run mode; destructive orphan cleanup is an
explicit `bounded_execution_unsupported` result because Trino's threshold-only
`remove_orphan_files` procedure could delete a larger or newer candidate set
than the reviewed plan. No orphan deletion is submitted, and the unsupported
result is not retry-safe. The reviewed counts describe snapshot or orphan
candidates only; they are not a ceiling on the provider's threshold-based
deletion surface.

### Dagster Performance

**Use multiprocess executor** for production:

```bash
# .phlo/.env.local
DAGSTER_EXECUTOR=multiprocess
```

**Configure resource limits**:

```python
# dagster.yaml
execution:
  multiprocess:
    max_concurrent: 4
    retries:
      enabled: true
      max_retries: 3
```

## Scaling

### Horizontal Scaling

**Trino workers**:

Add workers in `docker-compose.yml`:

```yaml
services:
  trino-worker-1:
    image: trinodb/trino:461
    environment:
      - TRINO_DISCOVERY_URI=http://trino:10005
    depends_on:
      - trino

  trino-worker-2:
    image: trinodb/trino:461
    environment:
      - TRINO_DISCOVERY_URI=http://trino:10005
    depends_on:
      - trino
```

**Dagster daemon replicas**:

```yaml
services:
  dagster-daemon-1:
    # ... configuration

  dagster-daemon-2:
    # ... configuration
```

### Vertical Scaling

**Resource limits** in `docker-compose.yml`:

```yaml
services:
  trino:
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 8G
        reservations:
          cpus: "2"
          memory: 4G

  postgres:
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 4G
```

### Storage Scaling

**MinIO distributed mode**:

```yaml
services:
  minio-1:
    image: minio/minio
    command: server http://minio-{1...4}/data{1...2}

  minio-2:
    image: minio/minio
    command: server http://minio-{1...4}/data{1...2}

  minio-3:
    image: minio/minio
    command: server http://minio-{1...4}/data{1...2}

  minio-4:
    image: minio/minio
    command: server http://minio-{1...4}/data{1...2}
```

## Security

### Access Control

**PostgreSQL roles**:

```sql
-- Read-only role for BI
CREATE ROLE bi_readonly WITH LOGIN PASSWORD 'secure-password';
GRANT CONNECT ON DATABASE cascade TO bi_readonly;
GRANT USAGE ON SCHEMA marts TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO bi_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO bi_readonly;

-- Application role with limited write
CREATE ROLE app_writer WITH LOGIN PASSWORD 'secure-password';
GRANT CONNECT ON DATABASE cascade TO app_writer;
GRANT USAGE ON SCHEMA bronze TO app_writer;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA bronze TO app_writer;
```

**MinIO policies**:

```bash
# Create read-only policy
mc admin policy create local readonly-policy policy.json

# policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::lake/*"]
    }
  ]
}

# Create user and assign policy
mc admin user add local readonly secure-password
mc admin policy attach local readonly-policy --user readonly
```

### Network Security

**Docker network isolation**:

```yaml
# docker-compose.yml
networks:
  backend:
    driver: bridge
  frontend:
    driver: bridge

services:
  postgres:
    networks:
      - backend

  dagster-webserver:
    networks:
      - backend
      - frontend
    ports:
      - "10006:10006" # Only expose webserver
```

**Firewall rules**:

```bash
# Allow only specific IPs to access services
iptables -A INPUT -p tcp --dport 10006 -s 10.0.0.0/8 -j ACCEPT
iptables -A INPUT -p tcp --dport 10006 -j DROP
```

### Secrets Management

**Use secret managers**:

```bash
# AWS Secrets Manager
export POSTGRES_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id phlo/postgres/password \
  --query SecretString --output text)

# HashiCorp Vault
export POSTGRES_PASSWORD=$(vault kv get -field=password secret/phlo/postgres)
```

**Docker secrets**:

```yaml
# docker-compose.yml
secrets:
  postgres_password:
    external: true

services:
  postgres:
    secrets:
      - postgres_password
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
```

## Monitoring

### Prometheus Metrics

**Enable Prometheus** in Dagster (via environment variables):

```bash
# .phlo/.env.local
DAGSTER_TELEMETRY_ENABLED=true
DAGSTER_PROMETHEUS_ENABLED=true
DAGSTER_PROMETHEUS_PORT=9090
```

**Key metrics to monitor**:

```promql
# Asset materialization success rate
rate(dagster_asset_materializations_total[5m])

# Asset materialization duration
histogram_quantile(0.95, rate(dagster_asset_materialization_duration_seconds_bucket[5m]))

# Failed materializations
rate(dagster_asset_materializations_failed_total[5m])

# Trino query duration
trino_query_execution_time_seconds

# MinIO storage usage
minio_disk_storage_used_bytes
```

### Grafana Dashboards

**Import dashboards**:

1. Start with observability profile:

```bash
phlo services start --profile observability
```

2. Access Grafana: http://localhost:3000

3. Import pre-built dashboards:
   - Dagster metrics
   - Trino performance
   - MinIO storage
   - PostgreSQL queries

### Alerting

**Configure Prometheus alerts**:

```yaml
# prometheus/alerts.yml
groups:
  - name: phlo_alerts
    rules:
      - alert: AssetMaterializationFailed
        expr: rate(dagster_asset_materializations_failed_total[5m]) > 0
        for: 5m
        annotations:
          summary: "Asset materialization failures detected"

      - alert: HighQueryLatency
        expr: histogram_quantile(0.95, rate(trino_query_execution_time_seconds_bucket[5m])) > 30
        for: 10m
        annotations:
          summary: "High Trino query latency"

      - alert: LowStorageSpace
        expr: (minio_disk_storage_free_bytes / minio_disk_storage_total_bytes) < 0.1
        for: 5m
        annotations:
          summary: "Low MinIO storage space"
```

**Slack integration**:

```bash
# .phlo/.env.local
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
SLACK_CHANNEL=#data-alerts
```

## Disaster Recovery

### Recovery Plan

**Recovery timing**:

Phlo does not publish an RTO or RPO commitment. The required recovery continuity
drill records the backup and restore durations it observes for its owned
PostgreSQL, Nessie/Iceberg, and object-store fixture, but those measurements do
not establish a production timing objective.

**Recovery steps**:

1. **Restore PostgreSQL**:

```bash
# Stop services
phlo services stop

# Restore database
gunzip < backup.sql.gz | docker exec -i phlo-postgres-1 psql -U postgres -d cascade

# Start services
phlo services start
```

2. **Restore MinIO**:

```bash
# Sync from backup
mc mirror /backups/minio/lake local/lake
```

3. **Verify Nessie catalog**:

```bash
# Check branches
phlo branch list

# Verify table metadata
curl http://localhost:10003/api/v1/trees/main
```

4. **Re-materialize recent partitions**:

```bash
# Last 7 days
for i in {0..6}; do
  date=$(date -d "$i days ago" +%Y-%m-%d)
  phlo materialize --partition $date
done
```

### Testing Recovery

A CI recovery drill exercises owned backup and restore using `scripts/recovery_drill.py`; live PostgreSQL migration evidence remains pending, so the upgrade_restore gate stays fail-closed.

## Release Management

Phlo uses standard git workflows with conventional commits and tags.

## Maintenance Windows

### Planned Downtime

**Communication**:

```bash
# Announce maintenance
curl -X POST $SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "#data-alerts",
    "text": "Scheduled maintenance: Phlo will be down 2025-01-15 02:00-04:00 UTC"
  }'
```

**Maintenance tasks**:

```bash
#!/bin/bash
# maintenance.sh

# 1. Stop Dagster daemon (prevent new runs)
docker stop phlo-dagster-daemon-1

# 2. Wait for running jobs to complete
while [ $(docker exec phlo-dagster-webserver-1 dagster job list --running | wc -l) -gt 0 ]; do
  sleep 60
done

# 3. Backup databases
docker exec phlo-postgres-1 pg_dump -U postgres cascade | gzip > backup_$(date +%Y%m%d).sql.gz
mc mirror local/lake /backups/minio/lake

# 4. Perform maintenance
docker exec phlo-postgres-1 psql -U postgres -d cascade -c "VACUUM ANALYZE;"

# 5. Optimize Iceberg tables
python -m phlo.maintenance.optimize_tables

# 6. Restart services
phlo services stop
phlo services start

# 7. Verify health
./health-check.sh

# 8. Announce completion
curl -X POST $SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "#data-alerts",
    "text": "Maintenance complete. Phlo is back online."
  }'
```

## Next Steps

- [Troubleshooting Guide](troubleshooting.md) - Common issues and solutions
- [Configuration Reference](../reference/configuration-reference.md) - Detailed configuration
- [Best Practices](best-practices.md) - Production patterns


## Plan-First Maintenance

Use `phlo operations maintenance` for v1 table maintenance (compaction, snapshot expiry):

```bash
phlo operations maintenance plan --operation compact --table <table> --ref <ref> --format json
phlo operations maintenance apply --plan <plan-file> --confirmation-token <plan-token>
```

Planning is read-only and returns a plan token. Apply is bound to that exact plan token and target revision; a stale or expired plan is rejected. Orphan deletion is unsupported. Verification is read-only.
