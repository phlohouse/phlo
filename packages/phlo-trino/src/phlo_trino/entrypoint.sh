#!/bin/sh
set -eu

if [ "$(id -u)" = 0 ]; then
    # Preserve the bind mount's group so the host can still regenerate config.
    # Change only the directory, not existing catalog files or their permissions.
    chown trino /etc/trino/catalog
    chmod u+rwx,g+rwx,o-w /etc/trino/catalog
    exec chroot --skip-chdir --userspec=trino:trino --groups=trino / "$@"
fi

exec "$@"
