#!/usr/bin/env bash
# Create scoped MinIO service policies for regulated deployments.
#
# Replaces shared root credentials with per-service access keys.
# Run this against a running MinIO instance with admin credentials.
#
# Prerequisites:
#   - mc (MinIO Client) installed
#   - MinIO alias configured: mc alias set phlo http://localhost:9000 <admin> <password>
#
# After creating policies, set env vars in .phlo/.env.local:
#   DAGSTER_MINIO_ACCESS_KEY=<dagster-access-key>
#   DAGSTER_MINIO_SECRET_KEY=<dagster-secret-key>
#
# See docs/setup/service-credentials.md for the full guide.

set -euo pipefail

ALIAS="${MINIO_ALIAS:-phlo}"

echo "Creating MinIO service policies..."

# Dagster service policy: read/write on lake bucket
cat > /tmp/phlo-dagster-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::lake",
        "arn:aws:s3:::lake/*"
      ]
    }
  ]
}
EOF

mc admin policy create "$ALIAS" phlo-dagster /tmp/phlo-dagster-policy.json
echo "  Created policy: phlo-dagster"

# Create dagster service account
# The secret is generated inline and never echoed or persisted by this script;
# MinIO cannot display it again afterwards, so re-running creates a new one.
mc admin user add "$ALIAS" phlo-dagster-svc "$(openssl rand -base64 24)"
mc admin policy attach "$ALIAS" phlo-dagster --user phlo-dagster-svc
echo "  Created user: phlo-dagster-svc with policy phlo-dagster"

# phlo-api service policy: read-only on lake bucket
cat > /tmp/phlo-api-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::lake",
        "arn:aws:s3:::lake/*"
      ]
    }
  ]
}
EOF

mc admin policy create "$ALIAS" phlo-api-reader /tmp/phlo-api-policy.json
echo "  Created policy: phlo-api-reader"

rm -f /tmp/phlo-dagster-policy.json /tmp/phlo-api-policy.json

echo ""
echo "Done. Set credentials in .phlo/.env.local:"
echo "  DAGSTER_MINIO_ACCESS_KEY=phlo-dagster-svc"
echo "  DAGSTER_MINIO_SECRET_KEY=<generated-password>"
