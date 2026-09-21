# Install Phlo

This page gets the `phlo` command working on your machine. When you finish, continue with [Build your first pipeline](first-pipeline.md).

## Prerequisites

- Python 3.12 or newer. Python 3.12 is tested in CI.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Docker with Compose v2. Podman with a Compose provider works in experiments but is not tested in CI.
- 8 GB of RAM and 20 GB of disk for the local stack.

On Windows, run Phlo inside a WSL 2 distribution and keep your project under the Linux filesystem, for example `~/projects`. Paths under `/mnt/c` make container bind mounts slow and unreliable.

## Install the CLI

Create a virtual environment and install Phlo with the default local stack:

```bash
mkdir phlo-workspace && cd phlo-workspace
uv venv
source .venv/bin/activate
uv pip install "phlo[defaults]"
```

The `defaults` extra installs the packages that the local stack needs: `phlo-dagster`, `phlo-dlt`, `phlo-pandera`, `phlo-dbt`, `phlo-iceberg`, `phlo-nessie`, `phlo-minio`, `phlo-trino`, `phlo-postgres`, `phlo-api`, `phlo-observatory`, `phlo-observe-plugin`, and `phlo-core-plugins`.

Check the install:

```bash
phlo --version
phlo init --list-templates
```

The second command prints the seven project templates that the installed packages provide: `minimal`, `basic`, `dbt-medallion`, `csv-batch`, `api-ingestion`, `observability-demo`, and `sling-replication`. If it prints only `minimal`, the provider packages did not install. Rerun the `uv pip install` command inside the activated environment.

## Return in a new terminal

The CLI environment lives in `phlo-workspace/.venv`. In each new terminal, reactivate it before running bare `phlo` commands:

```bash
cd phlo-workspace
source .venv/bin/activate
phlo --version
```

On PowerShell inside a native Windows environment, activation is `.venv\\Scripts\\Activate.ps1`; the supported Windows route for the local stack remains WSL 2. If you prefer not to activate the environment, prefix commands with `uv run`, for example `uv run phlo --version`.

## Install fewer packages

To install only the core and pick providers yourself, install `phlo` and then the packages you want:

```bash
uv pip install phlo phlo-dagster phlo-dlt phlo-pandera phlo-iceberg phlo-nessie phlo-minio phlo-trino
```

[Choose your stack](../guides/choose-your-stack.md) lists the providers for each layer and their support tier. [Packages](../reference/packages.md) lists every package.

## Use Podman instead of Docker

Podman is not tested in CI. If you want to try it, start the Podman machine and tell Phlo which backend to use:

```bash
podman machine start
export PHLO_CONTAINER_BACKEND=podman
```

On Linux, `podman machine start` is not needed.

## Uninstall

Inside a project, stop the stack and delete its volumes:

```bash
phlo services stop --volumes
```

Then delete the project directory and the virtual environment.
