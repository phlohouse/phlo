#!/bin/bash
# Phlo Dagster Entrypoint
# Syncs dependencies in dev mode before running the main command

set -eo pipefail

# Lock-aware image builds install the locked dependency set into
# /opt/phlo-project-venv (PATH already prefers it); installs for the mounted
# project must land in that venv rather than the system interpreter.
if [ -d /opt/phlo-project-venv ]; then
    uv_target=(--python /opt/phlo-project-venv/bin/python)
else
    uv_target=(--system)
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Dagster bootstrap must start as root so it can install mounted project dependencies." >&2
    exit 1
fi

# If dev mode is enabled, sync dependencies from mounted pyproject.toml
if [ "$PHLO_DEV_MODE" = "true" ] && [ -f /opt/phlo-dev/pyproject.toml ]; then
    echo "Dev mode: syncing dependencies from pyproject.toml..."
    # Install phlo into the target Python environment, degrading through weaker
    # install modes (defaults extra -> editable -> non-editable). A total
    # failure only warns rather than aborting bootstrap.
    cd /opt/phlo-dev
    uv pip install "${uv_target[@]}" -e ".[defaults]" 2>/dev/null || \
      uv pip install "${uv_target[@]}" -e . 2>/dev/null || \
      uv pip install "${uv_target[@]}" . || \
      echo "Warning: Could not sync dependencies"
    cd /opt/dagster
    echo "Dev mode: dependencies synced"
fi

# Optionally install extra local packages in dev mode
if [ "$PHLO_DEV_MODE" = "true" ] && [ -n "$PHLO_DEV_EXTRA_PACKAGES" ]; then
    echo "Dev mode: installing extra packages: $PHLO_DEV_EXTRA_PACKAGES"
    for pkg in ${PHLO_DEV_EXTRA_PACKAGES//,/ }; do
        if [ -z "$pkg" ]; then
            continue
        fi
        local_path="/opt/phlo-dev/packages/$pkg"
        if [ -d "$local_path" ]; then
            uv pip install "${uv_target[@]}" -e "$local_path" || echo "Warning: Could not install $pkg"
        else
            uv pip install "${uv_target[@]}" "$pkg" || echo "Warning: Could not install $pkg"
        fi
    done
fi

# Install the mounted user project so workflow imports and project dependencies
# are available before Dagster loads Definitions.
if [ -f /app/pyproject.toml ]; then
    echo "Installing mounted Phlo project..."
    # A generated project can declare an optional Phlo capability after the
    # stack has started (for example, phlo-pandera after creating an ingestion
    # workflow). Resolve only those direct workspace dependencies rather than
    # installing every package under /opt/phlo-dev.
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
    while IFS= read -r package; do
        if [ -z "$package" ]; then
            continue
        fi
        local_path="/opt/phlo-dev/packages/$package"
        if [ -d "$local_path" ]; then
            echo "Installing declared local package: $package"
            uv pip install "${uv_target[@]}" -e "$local_path"
        fi
    done <<< "$project_local_packages"
    # uv reads the mounted project's [tool.uv] configuration from its working
    # directory, not from the absolute editable target. Lock-aware builds keep
    # the locked dependency graph authoritative: install the project package
    # itself without re-resolving its dependencies.
    if [ -d /opt/phlo-project-venv ]; then
        (cd /app && uv pip install "${uv_target[@]}" --no-deps -e .)
    else
        (cd /app && uv pip install "${uv_target[@]}" -e .)
    fi
    echo "Mounted Phlo project installed"
fi

# Create sitecustomize.py to suppress Dagster SupersessionWarning at Python startup
# This runs before any Python script and filters out deprecated CLI warnings
SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
cat > "${SITE_PACKAGES}/sitecustomize.py" << 'EOF'
# Phlo: Suppress Dagster deprecation warnings for deprecated CLI commands
import warnings
try:
    from dagster import SupersessionWarning
    warnings.filterwarnings("ignore", category=SupersessionWarning)
except ImportError:
    pass
EOF

# Development stacks mount the complete repository. Make every source package
# importable even when the pinned runtime image runs as the unprivileged user
# and therefore cannot perform a system-wide editable install.
if [ "$PHLO_DEV_MODE" = "true" ] && [ -d /opt/phlo-dev ]; then
    for source_dir in /opt/phlo-dev/src /opt/phlo-dev/packages/*/src; do
        if [ -d "$source_dir" ]; then
            export PYTHONPATH="$source_dir${PYTHONPATH:+:$PYTHONPATH}"
        fi
    done
fi

# Execute Dagster from the mounted project root when available. User workflows
# often read local files relative to the project (for example data/*.csv), while
# DAGSTER_HOME intentionally remains /opt/dagster for instance state.
if [ -n "$PHLO_PROJECT_PATH" ] && [ -d "$PHLO_PROJECT_PATH" ]; then
    cd "$PHLO_PROJECT_PATH"
fi

# Signal that the bootstrap work is complete before the container drops
# privileges and starts Dagster. The host CLI waits for this marker.
touch /tmp/phlo-dagster-ready

# Drop privileges after the one-time bootstrap. Linux development stacks retain
# their host-owned project files; other stacks use the image's phlo account.
runtime_user="phlo"
runtime_home="/var/lib/phlo-runtime"
if [ -n "${PHLO_RUNTIME_UID:-}" ] && [ -n "${PHLO_RUNTIME_GID:-}" ]; then
    runtime_user="${PHLO_RUNTIME_UID}:${PHLO_RUNTIME_GID}"
fi
# Numeric runtime users do not have a passwd entry, so gosu resets HOME to "/"
# when dropping privileges. Prepare an identity-owned home before the privilege
# drop rather than sharing /tmp with root bootstrap or exec processes, and pass
# it through explicitly.
mkdir -p "$runtime_home"
chown "$runtime_user" "$runtime_home"
chmod 700 "$runtime_home"
exec gosu "$runtime_user" env HOME="$runtime_home" "$@"
