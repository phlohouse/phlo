#!/bin/bash
# Start the read-only preview Trino from the credentials phlo-api made for it.
# Runs as root only to copy those files into a private tmpfs owned by Trino's
# user, then drops to that user. Nothing here is writable by the query engine.
set -euo pipefail

source_dir=/var/lib/phlo-preview/server
templates=/etc/trino-preview
config=/etc/trino

if [ ! -s "$source_dir/targets" ]; then
    echo "trino-preview: phlo-api has not set up the query preview, usually because phlo.yaml has no environments. Not starting." >&2
    exit 0
fi

umask 077
mkdir -p "$config/catalog" "$config/preview" "$config/secrets"
cp "$templates/jvm.config" "$templates/node.properties" "$config/"
cp "$templates/access-control.properties" "$templates/password-authenticator.properties" \
    "$templates/resource-groups.properties" "$config/"
# The certificate is a PEM key and chain, so the keystore needs no password.
sed -e '/^http-server.https.keystore.key=/d' \
    -e 's#^http-server.https.keystore.path=.*#http-server.https.keystore.path=/etc/trino/secrets/server.pem#' \
    "$templates/config.properties.template" >"$config/config.properties"
# Every user on this server is a preview identity, so all share the bounded group.
sed -e 's/"phlo_api_preview_(prod|staging)"/"phlo_api_preview_.*"/' \
    "$templates/resource-groups.json" >"$config/preview/resource-groups.json"
cp "$source_dir/access-control.json" "$config/preview/access-control.json"
cp "$source_dir/password.db" "$config/secrets/preview-password.db"
cp "$source_dir/server.pem" "$config/secrets/server.pem"
cp "$source_dir/ca.pem" "$config/secrets/ca.pem"

# One read-only catalog per environment: the main Iceberg catalog pinned to
# that environment's Nessie ref.
while read -r catalog ref; do
    if ! [[ "$catalog" =~ ^[a-z_][a-z0-9_]*$ && "$ref" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "trino-preview: refusing catalog '$catalog' for ref '$ref'." >&2
        exit 1
    fi
    {
        grep -v -e '^iceberg.rest-catalog.prefix=' -e '^iceberg.security=' \
            /etc/trino-source/iceberg.properties
        echo "iceberg.rest-catalog.prefix=$ref"
        echo "iceberg.security=READ_ONLY"
    } >"$config/catalog/$catalog.properties"
done <"$source_dir/targets"

chown -R trino:trino "$config"
chmod 0755 "$config"
TRINO_INTERNAL_SHARED_SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '\n/+=')"
export TRINO_INTERNAL_SHARED_SECRET
exec chroot --skip-chdir --userspec=trino:trino --groups=trino / "$@"
