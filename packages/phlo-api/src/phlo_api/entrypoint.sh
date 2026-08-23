#!/bin/sh
# phlo-api container bootstrap. In dev mode the mounted phlo workspace is
# installed editable so container code matches host sources; otherwise this
# script is a pass-through that hands off to the image CMD.
set -e

install_editable() {
    package_path="$1"
    log_path="$2"
    # Install output goes to a log file so success stays quiet; the log is
    # printed only when the install fails.
    if ! uv pip install --system -e "$package_path" >"$log_path" 2>&1; then
        cat "$log_path"
        exit 1
    fi
}

    # Order matters: the workspace root pulls in shared dependencies, then
    # phlo-api itself (it may not be a root dependency), then every remaining
    # workspace package that defines a pyproject.toml.
if [ "$PHLO_DEV_MODE" = "true" ] && [ -f /opt/phlo-dev/pyproject.toml ]; then
    echo "Dev mode: installing local phlo workspace packages..."
    install_editable /opt/phlo-dev /tmp/phlo-api-dev-install.log
    if [ -f /opt/phlo-dev/packages/phlo-api/pyproject.toml ]; then
        install_editable /opt/phlo-dev/packages/phlo-api /tmp/phlo-api-package-install.log
    fi
    for package_dir in /opt/phlo-dev/packages/phlo-*; do
        if [ "$package_dir" = "/opt/phlo-dev/packages/phlo-api" ]; then
            continue
        fi
        if [ -f "$package_dir/pyproject.toml" ]; then
            package_name="$(basename "$package_dir")"
            install_editable "$package_dir" /tmp/"$package_name"-install.log
        fi
    done
    # Prepend the source trees so locally edited code shadows any copy already
    # installed into site-packages.
    export PYTHONPATH="/opt/phlo-dev/src:/opt/phlo-dev/packages/phlo-api/src:${PYTHONPATH:-/app}"
fi

exec "$@"
