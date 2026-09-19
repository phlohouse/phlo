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
    if ! uv pip install --python "$PHLO_DEV_PYTHON" -e "$package_path" >"$log_path" 2>&1; then
        cat "$log_path"
        exit 1
    fi
}

if [ "$PHLO_DEV_MODE" = "true" ] && [ -f /opt/phlo-dev/pyproject.toml ]; then
    echo "Dev mode: installing local phlo workspace packages..."
    # The container runs as the unprivileged phlo user whose home (/app) is a
    # read-only project mount, so neither uv's cache nor a system-site-packages
    # install can write there. Bootstrap targets a writable throwaway venv that
    # still sees the image's installed distribution set.
    export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/phlo-uv-cache}"
    # --clear because /tmp survives container restarts: a stale venv from a
    # crashed bootstrap must not shadow the current source mount.
    if ! uv venv --clear --system-site-packages /tmp/phlo-dev-venv >/tmp/phlo-dev-venv.log 2>&1; then
        cat /tmp/phlo-dev-venv.log
        exit 1
    fi
    PHLO_DEV_PYTHON=/tmp/phlo-dev-venv/bin/python
    # Order matters: the workspace root pulls in shared dependencies, then
    # phlo-api itself (it may not be a root dependency).
    install_editable /opt/phlo-dev /tmp/phlo-api-dev-install.log
    if [ -f /opt/phlo-dev/packages/phlo-api/pyproject.toml ]; then
        install_editable /opt/phlo-dev/packages/phlo-api /tmp/phlo-api-package-install.log
    fi
    # Honor the same extra-package convention as the other dev entrypoints.
    for package_name in $(echo "${PHLO_DEV_EXTRA_PACKAGES:-}" | tr ',' ' '); do
        if [ -z "$package_name" ]; then
            continue
        fi
        package_dir="/opt/phlo-dev/packages/$package_name"
        if [ -d "$package_dir" ]; then
            install_editable "$package_dir" /tmp/"$package_name"-install.log
        fi
    done
    # Install only the workspace packages the mounted project declares.
    # Enumerating every /opt/phlo-dev/packages/phlo-* directory would pull
    # dependency trees (for example clickhouse-connect -> lz4) that this
    # runtime image cannot compile.
    if [ -f /app/pyproject.toml ]; then
        project_local_packages="$(python - <<'PY'
import re
import tomllib

with open("/app/pyproject.toml", "rb") as project_file:
    dependencies = tomllib.load(project_file).get("project", {}).get("dependencies", [])

for dependency in dependencies:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", dependency)
    if match:
        name = match.group(0).lower()
        if name.startswith("phlo-"):
            print(name)
PY
)"
        echo "$project_local_packages" | while IFS= read -r package_name; do
            if [ -z "$package_name" ]; then
                continue
            fi
            package_dir="/opt/phlo-dev/packages/$package_name"
            if [ -d "$package_dir" ]; then
                install_editable "$package_dir" /tmp/"$package_name"-install.log
            fi
        done
        # The project itself cannot be editable-installed (its mount is
        # read-only), but its third-party dependencies must exist so the API
        # can exec project workflow files during capability discovery.
        project_requirements="$(python - <<'PY'
import re
import tomllib

with open("/app/pyproject.toml", "rb") as project_file:
    dependencies = tomllib.load(project_file).get("project", {}).get("dependencies", [])

for dependency in dependencies:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", dependency)
    if match and not match.group(0).lower().startswith("phlo"):
        print(dependency)
PY
)"
        echo "$project_requirements" | while IFS= read -r requirement; do
            if [ -z "$requirement" ]; then
                continue
            fi
            if ! uv pip install --python "$PHLO_DEV_PYTHON" "$requirement" >/tmp/project-dep-install.log 2>&1; then
                cat /tmp/project-dep-install.log
                exit 1
            fi
        done
    fi
    # The venv interpreter owns resolution; the CMD's bare `python` picks it up
    # through PATH so uvicorn serves the dev install, not the baked image one.
    export PATH="/tmp/phlo-dev-venv/bin:$PATH"
    export VIRTUAL_ENV=/tmp/phlo-dev-venv
    # Prepend the source trees so locally edited code shadows any copy already
    # installed into site-packages. /app stays last: project workflows load by
    # path and must never shadow installed distributions.
    export PYTHONPATH="/opt/phlo-dev/src:/opt/phlo-dev/packages/phlo-api/src:${PYTHONPATH:-/app}"
fi

exec "$@"
