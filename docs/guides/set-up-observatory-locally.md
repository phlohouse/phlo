# Set up Observatory locally

Create a loopback-only Observatory stack with one configured environment and
the canonical development identity. Install the Phlo API, Observatory, and the
service plugins for the generated stack in your project environment first.
Run these commands from the project root, not from `.phlo`.

This profile authenticates every request as `dev_user`. Use it only on a trusted
development machine. Do not expose it through a reverse proxy, a tunnel, or a
shared network. It is not a production login mechanism.

## Generate the development configuration

1. Choose local development once when initialising the services configuration:

   ```bash
   phlo services init --observatory-dev
   ```

   The command enables `phlo-api` and `observatory`, binds host ports to loopback,
   and writes the normal Compose and environment files without development source
   mounts. Use `--dev` separately only when developing Phlo itself. It also writes:

   - `phlo.yaml` with static development authentication, required default
     authorisation, and a single `sandbox` target if no environments exist.
   - `.phlo/authorization/roles.yaml` with the `dev_user` role assignment.
   - `.phlo/authorization/policies.yaml` with discovery and environment-scoped
     read permissions. V1 mutation permissions are not granted.

   Existing authentication, API authorisation, and policy files are not
   overwritten. Review an existing security configuration manually instead.
   `--production` and `--publish-all-interfaces` are incompatible with this profile.

2. Start the configured stack:

   ```bash
   phlo services start
   ```

   No repeated development flag is needed. The saved authentication provider and
   enabled services select the local onboarding flow. The command preserves
   generated files and existing data, starts the required services through the
   normal lifecycle, then checks the actual API identity, environment discovery,
   orchestration, incident storage, overview, and rendered UI. HTTP liveness
   alone is not readiness. Fresh API startup creates its supported schemas.
   Existing constraints that need migration `004` require the explicit upgrade
   procedure below; startup does not apply it.

   Only after these checks pass does the command print `Open Observatory: ...`
   and write `.phlo/observatory-ready.json`. Receipt schema version `1` includes
   `state: ready`, an ISO UTC `observed_at`, `env`, `url`, `capabilities`,
   `verification_method: development`, and `browser_login_verified: false`.
   The receipt records an observation, not a continuing health guarantee. Each
   startup invalidates the old receipt before preflight. The dashboard gate waits
   up to 180 seconds, with a 10-second timeout per HTTP request.
   Query preview is optional and can remain disabled.
   A readiness failure returns non-zero, preserves services and data, and prints
   diagnostic next steps. Retry ordinary `phlo services start` after repair.

   For an empty project, `phlo services start --observatory-dev` is a noninteractive
   first-run shorthand for the same persisted setup and start flow. It is not a
   separate authentication setting or required on later starts.

3. To inspect setup without starting services:

   ```bash
   phlo services preflight --observatory-dev --json
   ```

   A successful JSON envelope has `data.passed: true` and
   `data.live_dependencies_verified: false`. It checks configuration only.

4. Open the local Observatory address printed after the readiness gate.
   The bundled default publishes port `3001` on loopback. The browser does not
   need a fabricated token. The API's configured static provider resolves the
   development identity, and the normal authorisation backend evaluates policy.

   A backend outage must remain visible. Do not add a dummy environment to hide
   an unavailable target. Optional preview requires separate provider setup and
   is not enabled by this command. Use `phlo trino preview provision --help` and
   the preview provider's deployment guide to opt in with a dedicated coordinator,
   TLS, and private credentials. Additional roles and mutation grants require an
   explicit `phlo authz` setup. The generated identity has environment-scoped
   reads, not query permission.

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

Authentication belongs to an API deployment, not to a data environment selector.
Separate local and corporate deployments can use different providers. All
nonempty provider selections in `PHLO_AUTHENTICATION_PROVIDER`, legacy
`PHLO_AUTHENTICATION_METHOD`, and `phlo.yaml authentication.provider` must agree.
Conflicts fail startup. Provider-specific process settings override the
corresponding YAML provider fields. A shared API verifies identity before
environment-scoped authorisation. Changing `?env=` never changes the verifier,
enables development identity, or forwards credentials to another deployment.

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

## Build an authoring-enabled API image

The base API image does not install every provider. To retain and load reviewed
CSV files and use native SQL drafts, build with explicit provider extras:

```bash
export PHLO_API_EXTRAS=iceberg,dlt,trino,transform
phlo services init
phlo services start --build
```

Keep this selection in your generated project's environment configuration for
later rebuilds. Setting `PHLO_API_EXTRAS` on an already-built container does not
install providers. Development source mounts also use this explicit selection.
Add `dagster` or `lineage` only when the API needs those providers.

Install `phlo-dlt` in the Dagster code-location and run images too. The unlocked
Dagster image installs it through `phlo[defaults]`. For a lock-aware project,
declare `phlo-dlt` in the project's runtime dependencies, update its lockfile,
and rebuild Dagster. API-only installation cannot register an uploaded workflow
in another process.

The Python `phlo-transform` provider is not the native engine. To include an
approved engine in the API image, stage the executable inside the generated
`.phlo` build context and provide its separately verified SHA256:

```bash
export PHLO_TRANSFORM_BINARY=bin/phlo-transform
export PHLO_TRANSFORM_SHA256=REPLACE_WITH_VERIFIED_EXECUTABLE_SHA256
phlo services start --build
```

Use the executable's checksum, not its download archive's checksum. Select an
engine for the image architecture. The API uses the same Debian GNU-libc base
as Dagster, matching the portable Linux GNU releases. The build checks the
checksum and runs `phlo-transform --version` inside the final runtime. An
incompatible engine fails the build. Alternatively, mount an approved engine
under `.phlo/bin` read-only and verify its version inside the running image.
The build cannot validate a binary supplied only by a later runtime mount.

`phlo services init` and `start` prepare four private authoring directories:
`.phlo/uploads`, `.phlo/transform-editor`, `contracts/uploads`, and
`workflows/uploads`. The API mounts only these subtrees writable in addition to
its existing state, audit, settings, and log directories. The project root and
unrelated workflow and contract files remain read-only. Existing directory
permissions and ownership are not changed. Symlinked authoring directories are
rejected. Do not replace these mounts with a writable project-root mount.

On Linux, generate and start services as the account that owns the project.
The API and Dagster use that host UID and GID, so both can read private retained
bytes and generated definitions. If a remote code location runs as another
identity, provision access explicitly rather than making uploads world-readable.
Keep the same project-tree paths visible to its code-location and run containers.
After destination preparation, Dagster must reload the configured location to
discover `workflows/uploads/<id>.py` and its generated contract.

The API's candidate client connects to `trino:8080` inside Compose. Override
`PHLO_API_TRINO_HOST` and `PHLO_API_TRINO_PORT` for a different internal primary
coordinator. Native development retains `127.0.0.1` and the published
`TRINO_PORT` (default `10005`). These settings do not replace the separate
read-only preview binding. For native compilation, configure
`PHLO_TRANSFORM_ADAPTER=trino` and the transform preview endpoint, catalog, user,
password, and trusted CA to match the API preview binding. Keep candidate writes
on the writable primary coordinator. `CREATE CATALOG` writes on that coordinator;
the API does not need a writable catalog-directory mount.

### Check the installed-image authoring boundary

From a source checkout with `make setup` complete, run the bounded local probe
against the image you built:

```bash
uv run --locked python scripts/verify_observatory_authoring_image.py \
    --image YOUR_AUTHORING_API_IMAGE
```

The probe creates a disposable project and uses the generated API mounts under
the current UID. It retains and verifies two asymmetric CSV rows, authors the
contract and workflow, saves and reads a native SQL draft, and rejects unrelated
definition writes. A second installed container reads the retained CSV and
imports the generated ingestion module. To check your actual Dagster image's
provider installation, add `--worker-image YOUR_DAGSTER_IMAGE`.
The final output is `PASS: installed-image authoring boundary` with an explicit
reminder that this is not full service acceptance. The probe uses process-local
test settings storage, not production persistence.

### Complete a disposable service acceptance journey

Before enabling authoring for operators, use a disposable authenticated stack
with Postgres, writable Trino, Nessie, object storage, and a reloadable Dagster
location. Configure preview credentials, server-enforced preview limits, policy
grants, and the native engine before starting. Do not weaken those controls to
make this check pass.

1. Upload `id,label` with rows `7,retained` and `19,synthetic` in **Assets**.
   Verify the retained original bytes and review `id` as integer, `label` as
   string, with both non-nullable.
2. Prepare a unique destination. Verify the immutable contract and workflow in
   the shared project tree, and confirm the Dagster location reload succeeds.
3. Plan the candidate load. Review and confirm its current recipe and plan hash.
   Wait for the isolated Dagster run to finish, then preview the candidate.
4. Verify exactly those two rows, a real table snapshot, and `main_unchanged`.
   Read the candidate through the configured query engine and confirm the
   primary reference did not acquire the candidate table.
5. Open an existing native SQL model, change its SQL, and save a draft. Reload
   the revision and verify the saved SQL while the original source is unchanged.
   When preview is configured, compile and query it through the preview binding.

Record the installed image IDs, engine version, selected environment, candidate
reference, run ID, snapshot ID, and row readback without credentials. A container
health check or the bounded image probe does not establish these remote outcomes.
Production serving also has a separate executable check: from the Observatory
app directory, run `npm run build && npm run test:cache`. CI runs this HTTP suite
after its production build to cover cache headers and request-body limits.

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
