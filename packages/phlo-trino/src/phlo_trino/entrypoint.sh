#!/bin/sh
# Prepare persistent catalog permissions, then run the command as Trino's user.
set -eu

prepare_config_access() {
    # Catalog paths live in a runtime-writable directory. Never follow replacements
    # into root-private files on restart. Validate every path before changing any.
    for config in "$@"; do
        if [ -L "$config" ] || [ ! -f "$config" ]; then
            printf 'Trino config must be a regular file, not a symlink: %s\n' "$config" >&2
            exit 1
        fi
    done

    # A restrictive host umask can make generated config private to the host UID.
    # Keep that owner, granting Trino read access without exposing config to others.
    # The parent must not be writable by Trino: it contains this root-run script.
    chgrp trino /etc/trino
    chmod g+rx,go-w /etc/trino
    for config in "$@"; do
        chgrp trino "$config"
        chmod g+r,g-w "$config"
    done
}

if [ "$(id -u)" = 0 ]; then
    prepare_config_access \
        /etc/trino/config.properties \
        /etc/trino/jvm.config \
        /etc/trino/node.properties \
        /etc/trino/catalog/iceberg.properties \
        /etc/trino/catalog/iceberg_dev.properties \
        /etc/trino/catalog/delta.properties

    # Preserve the bind mount's group so the host can still regenerate config.
    # Leave all other catalog files and their permissions unchanged.
    chown trino /etc/trino/catalog
    chmod u+rwx,g+rwx,o-w /etc/trino/catalog
    exec chroot --skip-chdir --userspec=trino:trino --groups=trino / "$@"
fi

exec "$@"
