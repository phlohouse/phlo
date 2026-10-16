# Qualify a trusted-publishing release

Use this procedure to collect operational evidence for issue #987. Local tests
prove the implementation, not PyPI registration, secret removal, or publication.
The [frozen promotion contract](../architecture/decisions/0050-freeze-release-promotion-contract.md)
defines candidate identity and repeated-run qualification.

## Register the PyPI publisher

These steps change external configuration and require a project owner's approval.
For every PyPI project in the candidate BOM, register a GitHub trusted publisher
with these exact values:

- Owner: `phlohouse`
- Repository: `phlo`
- Workflow filename: `release-promotion.yml`
- Environment: `pypi`

Use an existing project's Publishing settings, or a pending publisher for a
project that does not yet exist. The environment is `pypi`, not `release`.
Do not register `release.yml` or `publish-minio-plugin.yml` as publishers.

List the current release-set projects before registration:

```bash
jq -r '.release_set.packages[].name' registry/support/v1.json | sort
```

The current list is `phlo`, `phlo-api`, `phlo-core-plugins`, `phlo-dagster`,
`phlo-dbt`, `phlo-dlt`, `phlo-iceberg`, `phlo-minio`, `phlo-nessie`,
`phlo-observatory`, `phlo-observe-plugin`, `phlo-pandera`, `phlo-postgres`, and
`phlo-trino`. Repeat this inventory for the actual candidate. Register any
additional project before its first publication. Every wheel and sdist in the
BOM uses the same publisher identity.

Only the promotion job grants `id-token: write` for Python publication.
`uv publish --trusted-publishing always` fails without OIDC credentials.
The script rejects long-lived token credentials before tagging or publication.
ReleaseX prepares version PRs but cannot publish. The former MinIO Python
publisher now validates packages and retains diagnostic artifacts only.
Those artifacts cannot substitute for a staged candidate or its evidence.

## Confirm the authority boundary

Inspect the GitHub `pypi` environment before publication. Do not assume that
the environment has required reviewers or branch restrictions. On 10 October
2026, read-only inspection found no protection rules or deployment branch
policy on either `pypi` or `release`.

The workflow still restricts dispatch to `phlohouse/phlo` on `main`. Its
authorization script validates the live workflow dispatch, workflow identity,
candidate, and evidence. For automatic publication, the human merger of the exact
ReleaseX PR is the release owner and that PR is the approval reference. A bot
cannot authorize publication of an ordinary merge. Explicit manual recovery
instead records the human dispatcher. `execute` defaults to `false`.
If additional environment protection is required, obtain separate approval
for that shared-settings change. This procedure does not grant that approval.

## Monitor automatic publication

**Merge the ReleaseX release PR. No further release action is required.**

Merging an in-repository ReleaseX PR from
`relx/release/monorepo/main-release-set` into `main` starts
**Release on merge**. The coordinator authenticates the exact merge SHA through
GitHub's PR association API and waits for its source-health aggregate. It then
dispatches the existing stage, acceptance, and promotion workflows on trusted
`main`. Two acceptance dispatches produce four architecture-specific bundles,
which exceed the unchanged three-bundle minimum. Promotion runs with
`execute=true` and retains its own dry-run, authorization, digest, and freshness
checks. The merge authorizes publication of those exact tested bytes; no second
manual dispatch or approval is part of the normal path. ReleaseX itself still
cannot publish. Ordinary merges and version-tag pushes do not publish.

### Artifact acceptance and production qualification

The staged BOM embeds the exact support-manifest text, verified against its
recorded digest. It selects the qualification requirements, not a workflow input
or a version-name guess:

- Explicit `alpha` or `beta` maturity with `production_ready: false` requires
  artifact acceptance. Security denial, ingestion, queries, transformations,
  WAP, maintenance, backup verification, and restore remain required.
  Production deployment preflight and supported-pair upgrade/recovery are not
  required and are never reported as passed.
- Every other candidate requires production qualification as well. Missing
  manifest text in a legacy BOM retains that stricter requirement.

Evidence records `qualification: artifact` or `qualification: production`.
Validation and promotion independently derive the expected qualification from
the BOM. Artifact evidence cannot qualify a production candidate, and no skipped
production check is recorded as passed. A production preflight failure or an
unavailable check still blocks a production release.

Artifact acceptance proves the tested packages and images work together. It does
not certify a deployment's OIDC, TLS, backend policy, or workload identities.
Publication does not change the support manifest's production-readiness claim.
The current upgrade implementation accepts only the 0.14.0 → 0.15.0 fixture
pair, not an upgrade to 0.18.0. A genuine 0.18.0 backup must never be relabelled
to satisfy that fixture. Production qualification remains blocked until a real
supported upgrade path exists for the candidate.

Monitor the coordinator and its downstream runs. It reuses successful or running
attempts on rerun, never restages a candidate, and stops on failed producers or
unexpected reruns. Source health has a two-hour deadline, each downstream phase
has a three-hour deadline, and the coordinator job has a six-hour limit.
Dispatch discovery stops after five minutes without retrying the write.
The `pypi` environment currently has no approval rules. Adding required reviewers
would introduce another human action and change this single-action contract;
it requires a separate, explicit settings decision.

## Recover manually and qualify the exact bytes

Obtain explicit authorization before dispatching workflows that publish images,
reserve candidates, or publish releases. Do not use a local test fixture as
release evidence.

Before recovery, inspect all existing runs and stop any active coordinator.
Reuse the successful stage run. A failed, deleted, or expired staging attempt
never permits rebuilding that SHA. A failed acceptance attempt remains part of
the evidence history and blocks promotion. Fix the cause in a new reviewed
candidate rather than hiding failed evidence. After a timeout, inspect the
dispatched workflow before rerunning the coordinator or dispatching manually.

1. Merge the reviewed implementation before attempting its operational proof.
2. Dispatch `release-stage.yml` on `main` with the full reviewed candidate SHA.
   Staging builds distributions once and records their hashes and image digests
   in `bom.json`. `build-core-services.yml` produces development images only.
3. Run the existing candidate acceptance workflow against that staging run.
   Collect at least three complete successful bundles on clean hosts. Each
   successful dispatch produces amd64 and arm64 bundles, so two dispatches suffice.
   They may execute on the same host on the same UTC day. The newest bundle must
   be at most seven days old.
4. Dispatch `release-promotion.yml` with that `candidate_sha` and `stage_run`,
   leaving `execute=false`. Review the dry-run receipt and all acceptance attempts.
5. After release-owner approval, dispatch the same candidate with `execute=true`.
   Promotion reauthenticates the evidence and uploads only the staged files.
6. Retain the successful promotion run URL, BOM, authorization, and receipt.

Promotion attaches `qualification-evidence-<run>-<attempt>.tar.gz` to the draft
release alongside the staged BOM and distribution bytes. The archive contains
the authorization, staged BOM and provenance, and all collected acceptance
evidence. Its canonical members are `authorization.json`, `bom.json`,
`provenance.json`, and `evidence/<relative bundle path>`. Before any publication,
the CLI checks their exact bytes against the adjudicated inputs, including
collected attempts that did not qualify. It checks provenance against the
candidate identity, BOM hash and supplied staging time. It rejects malformed
archives, missing or changed members, links, duplicate names and unsafe paths
without extracting the archive. The expected archive SHA-256 comes from the
same immutable bytes used for member validation. Promotion carries that digest
to the existing remote asset checks rather than rehashing the path later, so a
replacement between validation and upload cannot finalise the release.
An archive creation, upload or digest-check failure blocks release finalisation.
After successful finalisation, the workflow attaches the matched receipt as
`release-evidence-<run>-<attempt>.tar.gz`. A receipt archival failure does not
remove the qualification evidence already attached before finalisation.
Preserve both assets for the release's support life and at least 24 months.

The promotion receipt must report `status=promoted`, `success=true`, and matched
reconciliation. Its comparison records pair each candidate SHA-256 or image
digest with the observed public value. A signing or public-hash failure stops
before GitHub release finalisation. A failed publication requires forward
completion of the same bytes, not a rebuild.

## Verify signatures and public hashes

Promotion signs each immutable first-party service digest with keyless cosign.
Verification requires this exact certificate identity:

```text
https://github.com/phlohouse/phlo/.github/workflows/release-promotion.yml@refs/heads/main
```

The OIDC issuer is `https://token.actions.githubusercontent.com`. No key,
issuer-regexp fallback, or signature-verification bypass is configured.
Cosign verification output is recorded alongside the compared digests.

MinIO images remain independently versioned in `publish-minio.yml`. Its signing
job verifies the published manifest digest using that workflow's exact identity,
also on `refs/heads/main`. If signing fails after image publication, rerun the
failed signing job. Do not rebuild or overwrite the immutable image tag.

Run the daily `container-rescan.yml`, or obtain authorization for a manual
dispatch. It downloads the final release BOM and distributions, checks GitHub
asset hashes, PyPI hashes, the source tag, and service-image digests, and verifies
signatures before reporting matched publication. It also verifies the independent
MinIO signature. The vulnerability scan still runs if the publication or signature
audit fails. A legacy release without a BOM or signatures fails qualification;
the workflow does not invent or waive missing evidence.

On 10 October 2026, the latest release is `v0.17.0` with no assets, and real
cosign verification of its published API digest returns `no signatures found`.
After this implementation merges, the scheduled trust audit remains red until
a qualified new release supplies the BOM and valid signatures. This is an
intentional rollout consequence, not evidence that merge completes #987.

For a read-only audit of downloaded release assets, arrange the files as
`candidate/bom.json` and `candidate/distributions/*`, then run:

```bash
python3 scripts/promote_release_candidate.py verify-published \
  --candidate-bom candidate/bom.json --staging-dir candidate \
  --report-output publication-audit.json
```

This command requires Git, GitHub CLI, Docker Buildx, and cosign. It publishes,
signs, and rebuilds nothing. The report must contain the actual paired public
hashes and digests. Archive the report and signature output with the release
evidence, and link the CI run in #987.

## Remove obsolete publishing credentials

After verifying a real OIDC publication, obtain separate approval to revoke the
old PyPI API token and delete `PYPI_API_TOKEN` wherever it exists. Inspect repository,
organization, `release`, and `pypi` secrets, plus any other environment that
previously supplied publishing credentials. Remove equivalent long-lived upload
credentials as well. Code no longer referencing a secret does not prove deletion.

Repeat the read-only secret inventory with credentials authorized to inspect
those scopes. Current integration credentials returned HTTP 403 for repository
Actions secrets, so absence cannot be established from this checkout.

Keep #987 open until a real trusted-publishing run, exact-byte comparisons,
successful CI signature verification, and authorized credential removal are
linked. No release or shared configuration change is authorized by implementing
this code.
