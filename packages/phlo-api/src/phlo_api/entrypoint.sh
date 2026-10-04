#!/bin/sh
# phlo-api container bootstrap. In dev mode the mounted phlo workspace is
# installed editable so container code matches host sources; otherwise this
# script is a pass-through that hands off to the image CMD.
set -e

# Development Compose stacks can run with the host UID, which has no writable
# home directory in this image. Keep uv's transient cache in /tmp so editable
# provider installation can complete before the API starts.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/phlo-uv-cache}"
mkdir -p "$UV_CACHE_DIR"

install_editable() {
    package_path="$1"
    log_path="$2"
    # Install output goes to a log file so success stays quiet; the log is
    # printed only when the install fails. Dev containers run as the unprivileged
    # application user, so install into the writable temporary workspace rather
    # than the image's system site-packages.
    if ! uv pip install --target "$DEV_INSTALL_ROOT/site-packages" --reinstall -e "$package_path" >"$log_path" 2>&1; then
        cat "$log_path"
        exit 1
    fi
}

PHLO_DEV_ROOT="${PHLO_DEV_ROOT:-/opt/phlo-dev}"

if [ "$PHLO_DEV_MODE" = "true" ] && [ -f "$PHLO_DEV_ROOT/pyproject.toml" ]; then
    echo "Dev mode: installing local phlo workspace packages..."
    # The development workspace is mounted read-only; setuptools needs to
    # write egg-info during editable installation, so install writable copies.
    DEV_INSTALL_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/phlo-dev-install.XXXXXX")"
    mkdir -p "$DEV_INSTALL_ROOT/packages"
    mkdir -p "$DEV_INSTALL_ROOT/site-packages"
    if [ -f "$PHLO_DEV_ROOT/packages/phlo-postgres/pyproject.toml" ]; then
        cp -R "$PHLO_DEV_ROOT/packages/phlo-postgres" "$DEV_INSTALL_ROOT/packages/"
    fi
    if [ -f "$PHLO_DEV_ROOT/packages/phlo-api/pyproject.toml" ]; then
        cp -R "$PHLO_DEV_ROOT/packages/phlo-api" "$DEV_INSTALL_ROOT/packages/"
    fi
    chmod -R u+w "$DEV_INSTALL_ROOT"
    if [ -f "$DEV_INSTALL_ROOT/packages/phlo-postgres/pyproject.toml" ]; then
        install_editable "$DEV_INSTALL_ROOT/packages/phlo-postgres" /tmp/phlo-postgres-package-install.log
    fi
    if [ -f "$DEV_INSTALL_ROOT/packages/phlo-api/pyproject.toml" ]; then
        install_editable "$DEV_INSTALL_ROOT/packages/phlo-api" /tmp/phlo-api-package-install.log
    fi
    # Prepend the source trees so locally edited code shadows any copy already
    # installed into site-packages. Imports remain live from the read-only mount.
    export PYTHONPATH="$PHLO_DEV_ROOT/src:$PHLO_DEV_ROOT/packages/phlo-api/src:$PHLO_DEV_ROOT/packages/phlo-postgres/src:$DEV_INSTALL_ROOT/site-packages:${PYTHONPATH:-/app}"
fi

exec "$@"
