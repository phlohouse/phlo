# Installation Guide

Complete guide to installing and setting up Phlo on your system.

## Prerequisites

### Required

- **Python**: 3.11 or 3.12 for the currently tested support boundary
- **Container backend**: Docker with Docker Compose v2, which is the currently supported path; Podman with a Compose provider is documented as experimental and is not CI-supported

### Recommended

- **uv**: Fast Python package installer (recommended for better performance)
- **8GB RAM**: Minimum for running all services
- **20GB disk space**: For Docker volumes and data

## Windows via WSL

Phlo works best on Windows when you run it inside a WSL 2 Linux distribution and keep your project files on the Linux filesystem, for example under `~/projects`, instead of `/mnt/c/...`.

From an elevated PowerShell terminal on Windows:

```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
wsl --update
```

Restart if Windows asks you to, then open Ubuntu and prepare the Linux environment:

```bash
sudo apt update
sudo apt install -y build-essential ca-certificates curl git python3 python3-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL
uv python install 3.12
```

Install Docker Engine inside Ubuntu/WSL:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo service docker start
```

Allow your WSL user to run Docker without `sudo`, then restart your Ubuntu shell:

```bash
sudo usermod -aG docker $USER
exit
```

Reopen Ubuntu and verify Docker from inside WSL:

```bash
docker version
docker compose version
```

If your organization already standardizes on Docker Desktop, that also works: install Docker Desktop on Windows, enable the WSL 2 backend, enable integration for your Ubuntu distribution, then run the same Phlo commands from inside Ubuntu.

Now create and run Phlo from inside Ubuntu:

```bash
mkdir -p ~/projects
cd ~/projects
uv venv
source .venv/bin/activate
uv pip install "phlo[defaults]"
phlo init my-project --template csv-batch
cd my-project
uv pip install -e .
phlo services init
phlo services start
phlo services status
phlo doctor
```

Open browser UIs from Windows with the same localhost ports, for example http://localhost:10006 for Dagster.

Troubleshooting tips:

- If Docker commands fail inside Ubuntu, run `sudo service docker start`, then check your user is in the `docker` group with `groups`.
- If file watching or installs feel slow, move the project from `/mnt/c/...` into the WSL filesystem.
- If ports are busy on Windows, edit the `env:` ports in `phlo.yaml`, then rerun `phlo services init`.

## Native Windows with Docker Desktop

WSL is optional for the release artifact golden path. Install Python 3.11, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Docker Desktop, switch Docker Desktop to Linux containers, then run the launcher from a native PowerShell prompt at the repository root:

```powershell
.\scripts\release_golden_path.ps1 `
  -Partition 2025-01-15 `
  -ProjectDir "$PWD\.phlo-release-golden-path"
```

The launcher finds Python 3.11 through `py -3.11` or `PATH`, then delegates to the existing artifact-backed `scripts/release_golden_path.py` harness. Use `-WhatIf` to print the exact command without starting Docker:

```powershell
.\scripts\release_golden_path.ps1 `
  -Partition 2025-01-15 `
  -ProjectDir "$PWD\.phlo-release-golden-path" `
  -WhatIf
```

This lane requires Docker Desktop to be running with Linux containers; it does not configure Docker Desktop or require WSL.

The `Windows Compose portability` GitHub Actions lane checks the part that can run on a hosted Windows runner: it generates and commits the shared Compose layout on Linux, checks it out into a native PowerShell path containing spaces, installs the built CLI, and reruns `phlo services init --no-dev --allow-insecure` without changing the shared files. It validates Compose syntax with the runner's Docker CLI or a pinned standalone Compose CLI. GitHub-hosted Windows runners do not provide Docker Desktop or Hyper-V, so the lane cannot start containers or prove Windows bind mounts, persistence across restart, or service health. Those checks require a Windows Docker Desktop machine or a self-hosted runner.

## Quick Install

```bash
# Install Phlo with default services
uv pip install "phlo[defaults]"

# Initialize a new project
phlo init my-project --template csv-batch
cd my-project

# Install project-local dependencies
uv pip install -e .

# Initialize infrastructure (generates .phlo/.env and .phlo/.env.local)
phlo services init

# Start services
phlo services start

# Verify setup and recovery guidance
phlo doctor
```

## Detailed Installation Steps

### Step 1: Install Phlo

Install Phlo and its default services:

```bash
uv pip install "phlo[defaults]"
```

The `[defaults]` extra installs these core service packages:

- `phlo-core-plugins` - built-in sources and quality checks
- `phlo-api` - REST API used by Observatory and operator tooling
- `phlo-observatory` - browser UI service
- `phlo-dagster` - orchestration
- `phlo-dlt` - ingestion decorators and DLT execution
- `phlo-dbt` - dbt project integration and CLI helpers
- `phlo-pandera` - schema and quality contracts
- `phlo-iceberg` - Iceberg table-store capability
- `phlo-postgres` - metadata and application state database
- `phlo-minio` - S3-compatible local object storage
- `phlo-nessie` - Git-like catalog for Iceberg
- `phlo-trino` - SQL query engine

Verify installation:

```bash
phlo --version
```

### Step 2: Initialize a Project

Create a new Phlo project. Use `minimal` for an empty project or choose a starter template when you want runnable example files:

```bash
phlo init --list-templates
phlo init my-project --template csv-batch
cd my-project
uv pip install -e .
```

This creates:
```
my-project/
├── .env.example         # Local secrets template (.phlo/.env.local)
├── contracts/           # Contract snapshots and generated migration inputs
├── data/                # Local example/source data when the template needs it
├── plugins/             # Project-local plugin modules
├── workflows/           # Data ingestion workflows
│   ├── ingestion/
│   ├── schemas/
│   └── transforms/      # dbt transformations
│       └── dbt/
├── tests/               # Test files
└── phlo.yaml           # Project configuration
```

### Step 3: Configure Environment

Configure non-secret defaults in `phlo.yaml` and secrets in `.phlo/.env.local`:

```bash
phlo services init
```

Edit `phlo.yaml` (env:) and `.phlo/.env.local` with your settings. The defaults work for local development:

```bash
# phlo.yaml (committed)
env:
  POSTGRES_PORT: 10000
  MINIO_API_PORT: 10001
  MINIO_CONSOLE_PORT: 10002
  NESSIE_PORT: 10003
  TRINO_PORT: 10005
  DAGSTER_PORT: 10006

# .phlo/.env.local (not committed)
POSTGRES_PASSWORD=phlo
MINIO_ROOT_PASSWORD=minio123
SUPERSET_ADMIN_PASSWORD=admin
```

### Step 4: Start Services

Start the infrastructure services:

```bash
phlo services start
```

This starts the services declared by the `.phlo/docker-compose.yml` generated by `phlo services init`:

1. PostgreSQL (port 10000)
2. MinIO (ports 10001-10002)
3. Nessie (port 10003)
4. Trino (port 10005)
5. Dagster webserver (port 10006)
6. Dagster daemon

**First-time setup**: run `phlo services init` before `phlo services start` so `.phlo/docker-compose.yml`, `.phlo/.env`, and `.phlo/.env.local` exist.

### Container Backend

Phlo currently supports Docker with Compose v2 for the CI-tested local service path. Podman with a Compose provider is documented as an experimental alternative and is not CI-supported.

For the experimental, non-CI-supported Podman path:

```bash
podman machine start
podman compose version
export PHLO_CONTAINER_BACKEND=podman
```

On Linux, `podman machine` may not be required. On macOS and Windows, Podman uses a
machine because Linux containers require a Linux environment.

### Step 5: Verify Installation

Check service status:

```bash
phlo services status
phlo doctor
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

Access Dagster UI:

```bash
# Open in browser
open http://localhost:10006
```

### Step 6: Create and Run Your First Pipeline

Create a workflow using the interactive wizard:

```bash
phlo workflow create
```

Or materialize all example assets (if using a template with examples):

```bash
phlo materialize --select "*"
```

You can also use the Dagster UI at http://localhost:10006 to materialize assets.

## Installation Options

### Minimal Installation

Install only the core Phlo framework without service packages:

```bash
uv pip install phlo
```

Then install services individually as needed:

```bash
uv pip install phlo-dagster phlo-postgres phlo-trino
```

The root package currently exposes these extras:

| Extra | Intended use | Installs |
| --- | --- | --- |
| `phlo[defaults]` | Full local starter stack for most projects | core plugins, API, Observatory, Dagster, DLT, dbt, Pandera, Iceberg, Postgres, MinIO, Nessie, Trino |
| `phlo[core-services]` | Infrastructure-only core services | Dagster, Postgres, MinIO, Nessie, Trino |
| `phlo[openmetadata]` | Add OpenMetadata package to an existing install | OpenMetadata integration package |

### With Optional Services

Install additional package families directly. These are packages, not root `phlo[...]` extras:

```bash
# Business intelligence
uv pip install phlo-superset

# Observability stack
uv pip install phlo-otel phlo-clickstack phlo-alloy phlo-prometheus phlo-grafana phlo-loki

# API layers
uv pip install phlo-postgrest phlo-hasura

# Data catalog
uv pip install phlo-openmetadata
```

Start with service profiles:

```bash
# With observability (Prometheus, Grafana, Loki)
phlo services start --profile observability

# With API layer (PostgREST, Hasura)
phlo services start --profile api

# Multiple profiles
phlo services start --profile observability --profile api
```

Available profiles come from the installed service plugins and the generated `.phlo/docker-compose.yml`. Use `phlo services list` after `phlo services init` to see what your project can start.

### Native Services Mode

Run Observatory and phlo-api as native Python processes instead of Docker containers (useful on ARM Macs or for development):

```bash
phlo services start --native
```

This runs:
- All infrastructure services (Postgres, Trino, etc.) in Docker
- Observatory UI and phlo-api as Python subprocesses
- Better performance on non-Linux systems

### Development Mode

For developing Phlo itself, mount local source code:

```bash
phlo services init --dev --phlo-source /path/to/phlo
phlo services start
```

This mounts the phlo monorepo into the Dagster container and installs `phlo[defaults]` as an editable install, allowing you to modify Phlo's source code and see changes immediately.

### Production Deployment

For production readiness and hardening guidance, see [Production Readiness](../operations/production-readiness.md) and [Security Setup](../setup/security.md).

## Verify Components

### PostgreSQL

```bash
phlo postgres
```

### MinIO

Open http://localhost:10001 in browser

- Username: minioadmin
- Password: minioadmin

### Nessie

```bash
curl http://localhost:10003/api/v2/config
```

### Trino

```bash
phlo trino
```

## Troubleshooting

### Services won't start

Check the selected container backend is running:

```bash
docker ps
podman info
```

View logs:

```bash
phlo services logs -f
```

### Port conflicts

If ports are already in use, edit `phlo.yaml` (env:) to change port numbers:

```bash
POSTGRES_PORT=15432
MINIO_PORT=19000
# etc.
```

### Insufficient resources

Ensure the container backend has enough resources:

- **Docker Desktop**: Settings → Resources → 8GB RAM minimum
- **Podman Desktop**: Settings → Resources → 8GB RAM minimum
- **Linux**: Check `docker info` or `podman info` for resource limits

### Permission errors

On Linux, you may need to fix permissions:

```bash
sudo chown -R $USER:$USER .phlo/
```

## Uninstall

Stop and remove all services:

```bash
# Stop services
phlo services stop

# Remove volumes (deletes all data)
phlo services stop --volumes
```

Remove Phlo directory:

```bash
cd ..
rm -rf phlo
```

## Next Steps

- [Quickstart Guide](quickstart.md) - Run your first pipeline
- [CLI Reference](../reference/cli-reference.md) - Learn CLI commands
- [Configuration Reference](../reference/configuration-reference.md) - Advanced configuration
- [Troubleshooting](../operations/troubleshooting.md) - Common issues
