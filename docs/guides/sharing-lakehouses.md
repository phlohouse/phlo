# Sharing a development lakehouse

Keep shared configuration in `.phlo/`. Commit Compose, Dockerfiles, service
configs, and generated Git rules alongside `phlo.yaml`, `pyproject.toml`, and
`uv.lock`. Personal settings and runtime state stay ignored:

```text
.phlo/
  .gitignore                 tracked, selects shared files
  .gitattributes             tracked, LF text checkout
  docker-compose.yml         tracked shared base
  dagster/                   tracked Dockerfiles/configs
  trino/                     tracked configs
  compose.shared.yaml        optional tracked team overrides
  compose.linux.yaml         optional tracked OS overrides
  compose.windows.yaml
  compose.macos.yaml
  overrides/                 ignored
    .env                     generated local defaults
    compose.host.yaml        generated host identity/dev mounts
    compose.yaml             personal Compose changes
  secrets/                   ignored
    .env                     credentials
  volumes/                   ignored
  logs/                      ignored
```

The inner `.gitignore` uses an allowlist: undeclared files and runtime data are
ignored by default. Add custom shared artifacts with migration's `--include`,
or deliberately maintain the allowlist. Ignore rules do not untrack files that
were already committed.

## Migrate an existing project

```console
phlo services migrate --dry-run
phlo services migrate
```

Migration moves `.phlo/.env` to `overrides/.env`, `.phlo/.env.local` to
`secrets/.env`, and `.phlo/compose.local.yaml` to `overrides/compose.yaml`.
Shared Compose, Dockerfiles, entrypoints, and configs already in `.phlo/` stay
in place. Files are selected from installed service manifests; use repeatable
`--include service/custom.conf` for additional handwritten artifacts.

It updates Compose environment-file references and moves generated host user IDs
and core Phlo dev-source mounts into the ignored host overlay. It replaces the
root blanket `.phlo/` exclusion with rules that let the inner allowlist work.
Review shared files before staging: common inline credential keys are rejected,
but arbitrary handwritten files still need review for private values and
machine-specific paths. Secrets and personal overrides are never selected.

For the earlier root export layout, it also moves `compose.phlo.yaml` to
`.phlo/compose.shared.yaml`, OS layers to `.phlo/compose.<os>.yaml`, and
`phlo-runtime/` artifacts back into `.phlo/`. Identical duplicates are
consolidated; differing destinations cause a preflight error. File failures
roll back planned moves and writes. The command does not stage or commit files.

## Start another developer's checkout

Install the same packages with `uv sync --locked`, then run:

```console
phlo services init --no-dev
phlo services start
```

In a shared-layout checkout, plain `init` preserves existing shared Compose and
service files while creating local environment files and host overrides.
`init --force` deliberately regenerates shared files from package definitions
and `phlo.yaml`; review the Git diff before committing. Use `--dev` for local
Phlo source development: generated differences stay in `overrides/compose.host.yaml`.
Personal edits belong in `overrides/compose.yaml`, because Phlo regenerates the
host file.

Docker and Podman apply the shared base, optional shared and OS layers, generated
host overrides, then personal overrides. WSL selects Linux. All relative bind
mounts and build contexts resolve from `.phlo/`, even inside `overrides/`;
use `../data` for project data. Prefer named volumes or relative paths. Windows
absolute paths should use long mount syntax (`type: bind`, `source: 'C:/data'`,
`target: /data`). Shared text artifacts use LF checkout attributes.

Compose uses its normal merge rules, including special rules for port and
volume lists: a later list does not necessarily remove earlier entries.
Environment readers support old paths until migration. New local defaults come
from `overrides/.env`, followed by `secrets/.env`. Native services consume these
environment files but do not use Compose overrides.

Additional Compose layers remain development-only: production, staging, and
regulated environments reject them until security validation can inspect merged
configuration. This shares configuration, not running data or volumes.
