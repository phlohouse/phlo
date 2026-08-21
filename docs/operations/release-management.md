# Release Management

Use this runbook to cut alpha, beta, and future stable Phlo releases.

Phlo releases are driven by ReleaseX and GitHub Actions:

- `relx.toml` defines the release channels, versioning rules, and prerelease workspace behavior.
- `.github/workflows/release.yml` opens release PRs, tags merged release PRs, and publishes tagged artifacts to PyPI.
- [Release candidate protection](release-candidate-protection.md) defines the exact-SHA
  evidence gate required before a candidate can merge or be tagged.
- `.github/workflows/publish.yml` is a manual recovery workflow for PyPI publishing.
- `.github/workflows/build-core-services.yml` builds release images for `phlo-api` and Observatory when a GitHub Release is published.

The release workflow pins ReleaseX `v1.4.0`. ReleaseX prepares release PRs in an
isolated workspace: it updates package versions, synchronizes each provider's
bounded `phlo` compatibility range, updates checked support-manifest and
first-party image version references, refreshes `uv.lock`, and only then updates
the release branch.

## Release Channels

Phlo has two ReleaseX channels:

| Branch | Channel | Version shape | Purpose |
|---|---|---:|---|
| `main` | stable publish channel | `0.12.x` | Normal 0.x artifacts; the product remains alpha until v1 readiness gates pass |
| `beta` | beta | `0.10.0b1` | Prerelease validation, workshops, and release candidates |

ReleaseX treats `main` as the normal stable-artifact publish channel, as configured
in `relx.toml`. A non-prerelease `0.x` version is still an alpha product maturity
claim, so publishing it through that channel does not mean Phlo is production-ready.

Beta releases sync selected workspace package versions into the root `defaults` and
`core-services` extras. This matters because `phlo[defaults]` must resolve the
matching beta package set instead of mixing beta root packages with older stable
workspace packages.

## Before You Release

Start from a clean checkout of the branch you intend to release.

```bash
git status --short
relx validate
relx status --channel
relx release plan --json
relx release prepare --check
```

`relx release prepare --check` performs the complete local release-PR preparation
without pushing a branch, opening a PR, tagging, publishing, or creating a release.
Run it before reviewing changes to release configuration.

### Dependency Refresh Lane

Run dependency refreshes only as an explicit release-maintenance task. They are
not part of normal pull-request CI. GitHub Actions audits the locked Python and
Observatory dependencies every day; Renovate opens dependency remediation PRs
for available vulnerability fixes. A remediation PR runs the normal CI suite
and the full nightly workflow before merge review. Do not merge a remediation
PR until those checks have passed. The nightly workflow deliberately runs only
for trusted in-repository PRs because it needs service credentials; forked PRs
retain normal CI without those secrets.

```bash
gh workflow run "Dependency Refresh" --ref main -f lane=all
make dependency-refresh
make dependency-refresh-check
```

The refresh lane is intentionally split:

- Patch lane first: `ruff`, `pytest`, and `psycopg2-binary`.
- Risk-managed lane after patch CI is green: `dbt-core`, `pyarrow`,
  OpenTelemetry packages, `dagster`, `dagster-webserver`, `rich`, and
  `clickhouse-connect`.

Prefer one PR for the patch lane and a separate PR for the risk-managed lane.
For the patch lane, update only the selected low-risk packages and lockfile:

```bash
uv lock \
  --upgrade-package ruff \
  --upgrade-package pytest \
  --upgrade-package psycopg2-binary
make dependency-refresh-check
make check
```

For the risk-managed lane, use `make dependency-refresh LANE=risk-managed` to
inspect which manifests reference each package, then group related runtime
surfaces and run the targeted smoke/regression checks for the affected packages
before the broad release checks. Renovate mirrors this split with
`release-safe-python-patches` and `release-risk-managed-python-deps` groups, and
both groups require Dependency Dashboard approval before Renovate opens PRs.

If local ignored runtime folders exist under `packages/`, use a clean worktree for
ReleaseX checks. Generated local folders can confuse workspace discovery because
the real workspace also uses `packages/*`.

For a clean worktree:

```bash
git worktree add --detach /tmp/phlo-release-check HEAD
cd /tmp/phlo-release-check
relx validate
relx status --channel
```

## Stable Release

Use this flow for a normal release from `main`.

1. Merge product, fix, and release-worthy PRs into `main`.
2. Let the `Release` workflow run on the push to `main`.
3. Review the ReleaseX PR named like `chore(release): phlo <version> + <n> packages`.
4. Confirm the release PR only changes expected version files, `uv.lock`, and `CHANGELOG.md`.
5. Wait for CI on the release PR to pass.
6. Merge the release PR.
7. Confirm the `Release` workflow creates the tag and GitHub Release.
8. Confirm the publish job uploads or skips the expected PyPI artifacts.
9. Confirm the core service image workflow finishes if the release includes image changes.

Useful checks:

```bash
git fetch --tags
git tag --list 'v*' --sort=-creatordate | head
python -m pip index versions phlo
```

## Beta Release

Use the beta channel when you need a PyPI prerelease before cutting stable.

1. Create or update `beta` from the candidate commit.

```bash
git fetch origin
git checkout beta
git merge origin/main
git push origin beta
```

2. Let the `Release` workflow run on the push to `beta`.
3. Review the beta release PR against `beta`.
4. Confirm the version set uses beta versions, for example `phlo 0.10.0b1`.
5. Confirm the root extras point at selected beta package versions.
6. Merge the beta release PR.
7. Confirm the beta tag is created and the publish job uploads or skips expected artifacts.
8. Validate installation with explicit prerelease pins from the release PR body or from the package list.

Prefer exact beta pins over broad prerelease resolution:

```bash
uv venv /tmp/phlo-beta-check
source /tmp/phlo-beta-check/bin/activate
uv pip install --prerelease explicit \
  "phlo[defaults]==0.10.0b1" \
  "phlo-dagster==0.4.0b1" \
  "phlo-dlt==0.5.0b1" \
  "phlo-iceberg==0.4.0b1"
phlo --version
```

Adjust the package pins to match the beta release PR. Do not use
`--prerelease allow` for routine verification because it can pull unrelated
third-party prereleases.

## Finalize A Beta

When the beta has passed validation, finalize it into a stable release.

1. Bring the validated beta changes back to `main`.
2. Run a final ReleaseX dry run from a clean `main` checkout.

```bash
relx release pr --dry-run --finalize
```

3. Open the final release PR.

```bash
relx release pr --finalize
```

4. Review, merge, and verify the stable tag and publish job as in the stable release flow.

The finalize flow selects packages currently on prerelease versions and converts them to stable versions, even when they have no new file changes after the latest beta tag.

## Manual Publish Recovery

Use manual publish only when the normal tag publish did not complete.

There are two recovery paths:

- Run the `Release` workflow manually with the failed tag in `workflow_dispatch`.
- Run `Publish to PyPI` manually for a dry run, an explicit package list, or a targeted recovery.

For the `Release` workflow, provide the version tag, with or without `v`:

```text
v0.10.0
```

The publish job checks PyPI and removes artifacts that already exist before it calls `uv publish`, so rerunning after a partial publish is expected to be safe.

For targeted publishing, use the `packages` input in `Publish to PyPI`:

```text
phlo, phlo-dagster, phlo-dlt
```

Run a dry run first when recovering a partial publish.

## Tag Recovery

If ReleaseX updates the release PR but does not create the tag after merge:

1. Confirm the release commit is on `main` or `beta`.
2. Confirm the version files and changelog already contain the intended release version.
3. Create an annotated tag on the release commit.

```bash
git fetch origin
git checkout main
git pull --ff-only
git tag -a v0.10.0 -m "v0.10.0"
git push origin v0.10.0
```

4. Watch the `Release` workflow publish job.
5. If publish does not run or only partially completes, use manual publish recovery.

Use the `beta` branch and beta tag when recovering a beta release.

## Image Release Checks

The core service image workflow runs when GitHub publishes a Release. Watch it
alongside the PyPI publish job.

After image builds complete:

```bash
docker pull ghcr.io/phlohouse/phlo-api:<version>
docker pull ghcr.io/phlohouse/phlo-observatory:<version>
```

Use the version tag without the leading `v` if the image metadata workflow emits semver tags that way.

## Release artifact support boundary

This release procedure documents publishing and image checks; it does not establish support for exact image tags or digests. The v1 support boundary also excludes high-availability and multi-region deployment guarantees.

## Troubleshooting

### ReleaseX Sees Local Generated Folders

Symptom:

```text
error: monorepo package packages/.phlo has no supported version files
```

Cause: the local checkout has ignored runtime folders under `packages/`.

Fix: use a clean worktree for ReleaseX checks, or remove the ignored local folder if it is safe to do so.

### PyPI Shows A Version Before `pip` Can Install It

PyPI package JSON can update before the simple index used by installers catches up.
Wait a few minutes and retry the exact install command.

### Beta Install Pulls Unrelated Prereleases

Use `--prerelease explicit` and exact beta pins for Phlo packages. Avoid
`--prerelease allow` unless you intentionally want prereleases from the full
dependency graph.
