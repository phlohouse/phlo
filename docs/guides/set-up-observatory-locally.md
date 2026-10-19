# Set up Observatory locally

Create a loopback-only Observatory stack with one configured environment and
the canonical development identity. Install the Phlo API, Observatory, and the
service plugins for the generated stack in your project environment first.
Run these commands from the project root, not from `.phlo`.

This profile authenticates every request as `dev_user`. Use it only on a trusted
development machine. Do not expose it through a reverse proxy, a tunnel, or a
shared network. It is not a production login mechanism.

## Generate the development configuration

1. Initialise the existing services configuration:

   ```bash
   phlo services init --observatory-dev --no-dev
   ```

   The command enables `phlo-api` and `observatory`, binds host ports to loopback,
   and writes the normal Compose and environment files. `--no-dev` disables
   source mounts, not the explicit development identity. It also writes:

   - `phlo.yaml` with static development authentication, required default
     authorisation, and a single `sandbox` target if no environments exist.
   - `.phlo/authorization/roles.yaml` with the `dev_user` role assignment.
   - `.phlo/authorization/policies.yaml` with discovery and environment-scoped
     read permissions. V1 mutation permissions are not granted.

   Existing authentication, API authorisation, and policy files are not
   overwritten. Review an existing security configuration manually instead.
   `--production` and `--publish-all-interfaces` are incompatible with this profile.

2. Check the generated setup:

   ```bash
   phlo services preflight --observatory-dev --json
   ```

   A successful JSON envelope has `data.passed: true` and
   `data.live_dependencies_verified: false`.
   The check distinguishes missing identity provider, role files, policy files,
   and environment mapping. It does not prove that a backend is ready.

3. Start the generated services:

   ```bash
   phlo services start
   phlo services list
   ```

4. Open the local Observatory address shown by the service configuration.
   The bundled default publishes port `3001` on loopback. The browser does not
   need a fabricated token. The API's configured static provider resolves the
   development identity, and the normal authorisation backend evaluates policy.

   A backend outage must remain visible. Do not add a dummy environment to hide
   an unavailable target. Optional preview requires separate provider setup and
   is not enabled by this command.

## Configure deployed targets

Set stable project-chosen environment IDs in `phlo.yaml`. A single entry is valid:

```yaml
default_environment: sandbox
environments:
  sandbox:
    dagster_location: phlo_dagster.framework.definitions
    nessie_ref: main
    compose_project: my-project
```

Replace `compose_project` with the generated project's name. For an existing
deployment, replace the location and reference with the actual deployed values.
Environment IDs must match `[a-z][a-z0-9_-]{0,62}`. Each target needs a distinct
Dagster location and Nessie reference. Configured Compose projects must also be
distinct. Arbitrary Nessie branches do not become deployed environments.

The API and UI use this same configuration. `default_environment` selects the
initial target even when its backend is unavailable. Without an explicit default,
configuration order selects the first authorised target. A valid selected target
stays selected during an outage. Removed selectors redirect to the configured
default, but a configured selector denied by policy remains an access error.
Zero authorised targets produce an access error, never a fabricated fallback.

The legacy `PHLO_V1_ENVIRONMENTS` runtime variable remains a complete replacement
for the YAML map. It never merges entries. An explicit YAML default must exist in
that replacement. Prefer YAML for human configuration, and remove stale runtime
overrides before changing it.

Names do not grant permissions or select production security mode. Update
environment-scoped policy resources such as `env=sandbox` and `env=sandbox|*`
when adding targets. Deployment security still uses `PHLO_ENVIRONMENT` and
regulated mode. A target's `nessie_ref` binds Observatory reads and governed
operations. It does not itself change the branch-WAP launch and publication
implementation. Review WAP routing separately before using another publication ref.

For cross-environment promotion, configure distinct source and target IDs:

```yaml
promotion:
  source_environment: sandbox
  target_environment: warehouse
```

Both IDs must exist in `environments`. Existing promotion worktree, reference,
signature, check evidence, and protected-mutation requirements still apply.
Legacy `prod` and `staging` maps retain their existing promotion roles when no
explicit promotion configuration exists. A single target has no promotion source.

## Upgrade an existing incident and query database

Fresh database schemas accept bounded environment IDs. Existing databases with
the old `prod` and `staging` CHECK constraints need explicit migration `004`.
Application startup does not apply this ALTER migration.

1. Select the installation's database using `PHLO_RUN_EVIDENCE_DB_URL`, then check:

   ```bash
   python -m phlo_api.migrations --check --json
   ```

   The read-only command emits `migration: "004_configurable_environments"`
   and `state: "compatible"` with exit code `0`, or `state: "required"` with exit
   code `1`. Connection errors fail without printing the database URL.

2. Before applying, take and verify a restorable database backup. Stop API
   writers and scheduled incident or usage writers for this installation.
   Use an operator identity authorised for `services.migrate` where CLI
   authorisation is enforced. Do not use container health as a migration check.

3. Apply and recheck:

   ```bash
   python -m phlo_api.migrations --apply --backup-confirmed --json
   python -m phlo_api.migrations --check --json
   ```

   In a generated API container, run the same module through
   `docker compose exec -T phlo-api python -m phlo_api.migrations --check --json`,
   using the installation's Compose files and environment-file options. The
   container already receives the selected database URL. Do not paste it into logs.

The migration replaces provider-owned environment constraints transactionally,
validates existing rows, and preserves keys and identity strings. A lock timeout
or invalid row aborts the transaction. Restore the verified backup if recovery
requires reverting schema and data. Reverting code alone does not undo the migration.
