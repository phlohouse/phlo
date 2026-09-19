# Install Phlo

This page gets the `phlo` command working on your machine. When you finish, continue with [Build your first pipeline](first-pipeline.md).

## Prerequisites

- Python 3.11 or 3.12. Newer versions can resolve but are not tested in CI.
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

The `defaults` extra installs the packages that the local stack needs: `phlo-dagster`, `phlo-dlt`, `phlo-pandera`, `phlo-dbt`, `phlo-iceberg`, `phlo-nessie`, `phlo-minio`, `phlo-trino`, `phlo-postgres`, `phlo-api`, `phlo-observatory`, and `phlo-core-plugins`.

Check the install:

```bash
phlo --version
phlo init --list-templates
```

The second command prints the eight project templates that the installed packages provide. If it prints only `minimal`, the provider packages did not install. Rerun the `uv pip install` command inside the activated environment.

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
