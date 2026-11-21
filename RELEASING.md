# Releasing

[ADR 0068](docs/architecture/decisions/0068-release-publishing.md) defines the
release contract. In short: the release build and smoke run continuously, and a
release publishes exactly the bytes its own smoke tested.

## Release

**Merge the ReleaseX release PR. Nothing else is required.**

Before merging, check that the PR's **Release Rehearsal** is green. It runs the
same build and artifact golden path as the release, on the release PR itself.

Merging the PR from `relx/release/monorepo/main-release-set` into `main` starts
**Release** (`release-promotion.yml`). It runs on every push to `main` and
continues only for the exact merge commit of that PR. Then it:

1. runs the release-mode dependency assessment (`security.yml`), and in
   parallel builds the sdists, wheels and images once (`release-build.yml`),
   staging any package whose version is already on PyPI from PyPI's bytes;
2. pins them into `bom.json` and runs the artifact golden path against them on
   amd64 (blocking) and arm64 (reported, never blocking). Its three parts (the
   core journey, the fresh Observatory install and the retained-volume upgrade)
   run on separate runners, and one job merges and validates their evidence;
3. attests build provenance for the distributions;
4. creates the `v<version>` tag on the release commit;
5. uploads the distributions to PyPI through trusted publishing;
6. tags each tested image digest with the version, then signs and verifies it
   with keyless cosign;
7. publishes the GitHub release with the changelog section, `bom.json` and the
   distributions;
8. opens or updates the "ci: release arm64 smoke is failing" issue if arm64 was
   red.

A typical release takes about an hour. Ordinary merges and tag pushes publish
nothing.

## Resume a failed release

Every publish step first reads what is already public. A file or tag that
exists with the tested digest is skipped. One that exists with different bytes
stops the release before anything else is uploaded.

- **The smoke or an infrastructure step failed.** Fix the cause in a normal PR.
  The next ReleaseX PR carries the fix. Nothing was published.
- **Publishing failed partway** (a PyPI outage, a registry error). Use
  **Re-run failed jobs** on the same run. It reuses the built artifacts and
  completes the release forward.
- **The release workflow itself needs a fix.** Merge the fix, then dispatch
  **Release** on `main` with the release commit SHA. That run rebuilds, so it
  can only complete a release whose public files match the rebuild. If it stops
  on a mismatch, decide by hand: yank and release a patch version, or delete
  the unpublished tag and rerun.

Never delete a PyPI file and upload different bytes under the same version.
PyPI forbids it, and the workflow refuses it.

## Qualification scope

`bom.json` embeds the exact support-manifest text. It selects what the smoke
must demonstrate:

- `alpha` or `beta` maturity with `production_ready: false` runs artifact
  qualification. Security denial, ingestion, queries, transformations, WAP,
  maintenance, backup verification and restore are required. Production
  preflight and supported upgrade and recovery are skipped and never reported
  as passed.
- Every other release, including `1.0.0`, must also pass production preflight
  and a real supported upgrade and recovery path.

Setting `production_ready: true` is a reviewed change on `main` that lands
before the release PR. Publishing never changes the support manifest.

## Version the blessed_core set in lockstep

Below `1.0.0`, ReleaseX versions each package from its own conventional
commits. From `1.0.0`, every `blessed_core` package shares one version
([ADR 0053](docs/architecture/decisions/0053-what-1-0-promises.md) decision 4).

ReleaseX has no shared-version group. Its `--next-version` option applies one
version to every selected package. The root package owns every path, and
`cascade_bumps` selects every package that depends on `phlo`, so each release PR
selects the whole workspace. Optional, preview, and development packages
therefore take the shared version too. The version number does not extend the
1.0 promise to them; the support manifest profiles define coverage.

On `main`, the release workflow passes the output of
`scripts/release_lockstep_version.py` to ReleaseX as `next-version`:

| Repository state | `next-version` |
| --- | --- |
| Root version below `1.0.0`, `production_ready: false` | Empty: independent versions |
| Root version below `1.0.0`, `production_ready: true` | `1.0.0` |
| Root version at or above `1.0.0` | The root package's planned version, the largest bump in the set |

The value is empty when no release is pending. ReleaseX rejects `next-version`
on prerelease channels, so `beta` release PRs keep independent versions. From
`1.0.0`, the validator rejects such a PR.

To release `1.0.0`:

1. Land the reviewed support-manifest change, together with its
   `make docs-reference-generate` output. Do not edit package versions.
2. The next **Release PR** run proposes `1.0.0` for every workspace package.
   Confirm that it does and that its Release Rehearsal passes.
3. Merge the release PR.

## PyPI trusted publishers

For every PyPI project in the release set, a GitHub trusted publisher is
registered with these exact values:

- Owner: `phlohouse`
- Repository: `phlo`
- Workflow filename: `release-promotion.yml`
- Environment: `pypi`

Every workspace package is in the release set and publishes with each release;
`validate_support_manifest.py` fails if one is missing. Publication does not
extend the support promise: each package's scope does that. Register any new
project before its first release, using a pending publisher.
List the release set with:

```bash
jq -r '.release_set.packages[].name' registry/support/v1.json | sort
```

Only the publish job grants `id-token: write`. The publish script refuses
long-lived token credentials. ReleaseX prepares version PRs but cannot publish.

## Verify a published release

Images are signed with this exact certificate identity:

```text
https://github.com/phlohouse/phlo/.github/workflows/release-promotion.yml@refs/heads/main
```

The OIDC issuer is `https://token.actions.githubusercontent.com`. To check an
image and a distribution by hand:

```bash
cosign verify \
  --certificate-identity https://github.com/phlohouse/phlo/.github/workflows/release-promotion.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/phlohouse/phlo-api:<version>
gh attestation verify phlo-<version>-py3-none-any.whl --repo phlohouse/phlo
```

MinIO images are versioned independently in `publish-minio.yml` and signed
with that workflow's identity on `refs/heads/main`.

**Container Rescan** runs weekly. It rescans the latest published fleet with
fresh vulnerability data and verifies every first-party signature.
