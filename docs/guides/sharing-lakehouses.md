# Sharing a development lakehouse

Commit `phlo.yaml`, `pyproject.toml`, and `uv.lock` so teammates install the same
service packages (`uv sync --locked`) and share service selection and non-secret
defaults. Run `phlo services init --no-dev` after cloning, or add `--force` after
changing those inputs. Keep `.phlo/` ignored: it contains secrets, local data,
generated service files, and host-specific configuration.

Put Compose customizations in **`compose.phlo.yaml` in the project root** instead
of editing `.phlo/docker-compose.yml`. Phlo's Docker and Podman commands apply:

1. `.phlo/docker-compose.yml` (generated base)
2. `compose.phlo.yaml` (shared team settings)
3. `compose.phlo.windows.yaml`, `compose.phlo.linux.yaml`, or
   `compose.phlo.macos.yaml` (only the OS running Phlo; WSL selects Linux)
4. `.phlo/compose.local.yaml` (optional personal settings)

These layers are development-only: Phlo rejects them in production, staging,
or regulated environments because existing security checks inspect the base
configuration. Use `phlo.yaml` for those deployments.

Missing layers are skipped. Commit the shared and OS layers. Changes take effect
on the next Compose command; use `phlo services start` to reconcile containers.
Regenerating infrastructure leaves these files intact. Native services do not
use Compose and therefore do not consume these layers.

```yaml
# compose.phlo.yaml
services:
  postgres:
    mem_limit: 1g
```

Compose merges these files using its normal rules: maps merge, while some lists
(such as ports and volumes) have special merge behavior. Do not assume that a
later list removes earlier entries. Keep service selection in `phlo.yaml`.

All relative bind mounts and build contexts resolve from **`.phlo/`**, the first
Compose file's directory, even in root-level overrides. Use `../data` for the
project's data directory. Prefer relative paths or named volumes for portability.
For machine-specific absolute paths use long mount syntax in the local layer
(`type: bind`, `source: 'C:/data'`, `target: /data`) to avoid Windows drive-letter
ambiguity. Container paths still use Linux syntax for Linux containers.

Store credentials in `.phlo/.env.local`, never tracked Compose files. Share
variable names and setup instructions, not values. Existing projects can move
handwritten changes into the shared layer, review them for secrets and absolute
paths, then regenerate with `phlo services init --force --no-dev`. Back up any
manual edits first. For deliberate local Phlo source development, use `--dev`;
this generates machine-specific mounts and should not be used as a shared base.

This shares configuration, not running volumes or database contents. Locking
Python packages does not pin mutable container tags; use image digests in shared
Compose settings when exact image reproducibility is required.
