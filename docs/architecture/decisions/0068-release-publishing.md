# ADR 0068: Release publishing

## Status

Accepted (2026-11-20). Supersedes [ADR 0050](0050-freeze-release-promotion-contract.md).

- Evidence: the 0.19.0 release took five days from the release PR merge (#1146,
  15 November) to PyPI (20 November). Four candidates were burnt and six fix PRs
  were needed. Every failure was a real defect (#1286, #1290, #1294, #1296), but
  each was found on release day because the full release path ran about once a
  month. Repeating a passing run found nothing a single run missed.
- Decision owner: Phlo project maintainers

## Context

ADR 0050 froze a release contract built for an organisation with separate
builders, testers and approvers. It used five workflows (Release on merge,
Release Stage, two Release Artifact Acceptance dispatches and Release
Promotion) plus a daily Container Rescan. Those were joined by about 3,600 lines
of BOM, evidence, authentication and promotion Python. The design required three
qualifying runs, a canonical candidate digest, a 24-month evidence archive, a
daily public-hash reconciliation and one staging attempt per commit.

Phlo has one maintainer. In practice the controls that protected users were
the parts that held up:

- publishing the exact bytes that were tested, never a rebuild;
- trusted publishing to PyPI and keyless cosign signatures on images;
- forward completion after a partial publish;
- the artifact golden path and `check_published_extras`, which caught #1290
  and #1294.

The parts that cost the most protected nothing that these do not. Repeat runs
and staging reservations turned each defect into a burnt candidate. The
reconciliation was red on 21 of 21 runs.

## Decision

1. **Build and smoke continuously.** `release-build.yml` builds the sdists,
   wheels and images once, pins them into a BOM and runs the artifact golden
   path against exactly those bytes. Release Rehearsal runs it on every ReleaseX
   PR, on PRs that change release machinery, and nightly on `main`. The release
   runs the same jobs. A rehearsal BOM is tagged `rehearsal-<sha>` and is
   refused by publishing.
2. **Unchanged packages ship PyPI's bytes.** ReleaseX bumps only packages that
   changed. A release-set package whose pinned version is already on PyPI is
   staged from PyPI in a release, so the smoke tests what users already install
   and publishing never meets a rebuilt duplicate. A rehearsal always rebuilds,
   so it tests the current tree.
3. **One workflow publishes.** `release-promotion.yml` runs on every push to
   `main` and continues only for the exact merge commit of the in-repository
   ReleaseX PR. That merge is the approval. The workflow runs the release-mode
   dependency assessment ([ADR 0063](0063-dependency-and-container-vulnerability-policy.md))
   alongside the build and smoke, and publishes only when both pass, in order: the `v<version>` tag on the release commit, PyPI, image
   tags by digest with cosign signatures, then the GitHub release with the BOM
   and distributions. The publish job stays inline in that file, on
   `refs/heads/main`, in the `pypi` environment. PyPI trusted publishers and the
   cosign identity `…/release-promotion.yml@refs/heads/main` are bound to it.
4. **Resume, never rebuild silently.** Every publish step first reads what is
   already public. A file or tag that exists with the tested digest is skipped.
   One that exists with different bytes stops the release before anything else
   is uploaded. PyPI read-after-write is retried. "Re-run failed jobs" therefore
   completes a partial release forward from the same artifacts. To apply a
   workflow fix, merge the fix and dispatch the workflow with the release commit
   SHA. That rebuilds, so it can only finish a release whose public parts match
   the rebuild.
5. **Provenance through attestations and signatures.** Distributions get a
   GitHub build-provenance attestation (`actions/attest-build-provenance`), and
   images get keyless cosign signatures. Both bind the bytes to the commit and
   this workflow. They replace the canonical candidate digest, the evidence
   bundles as a release gate, and the 24-month archive. The golden path still
   writes its evidence bundle as the smoke report, kept with the run.
6. **One smoke run, amd64 blocking, arm64 reported.** The amd64 golden path
   must pass before publishing. arm64 runs in parallel in every release, does
   not block it, and opens or updates an issue when it fails.
7. **Qualification scope is unchanged.** A release whose support manifest says
   `alpha` or `beta` with `production_ready: false` runs artifact
   qualification, which skips production preflight and the supported upgrade and
   recovery demonstrations. Every other release must also pass those. The smoke
   records the scope, and artifact qualification never stands in for production
   qualification.
8. **Published images are rescanned weekly.** Container Rescan runs weekly
   with fresh vulnerability data and verifies image signatures. The daily
   byte reconciliation is dropped, because publishing already verifies every
   public digest.
9. **Revocation is manual and forward.** A defect found after publishing is
   fixed in a new release. When a release must be withdrawn, yank it on PyPI and
   delete its image tags. Digests are never reused.

## Consequences

- A broken release path shows up within a day, on the rehearsal, instead of on
  release day.
- A release is one workflow run of about an hour, and a failure can be resumed
  without a new release PR.
- Publishing fails closed on any public difference, so a resumed or dispatched
  run can never publish different bytes under an existing version. A rebuilt
  image that differs from an already-pushed tag needs a human to resolve it.
- Only one amd64 smoke run gates a release. A failure that would only appear on
  a second run, or only on arm64, can reach users. arm64 failures are still
  reported on every release.
- [ADR 0053](0053-what-1-0-promises.md)'s 1.0 exit gate now refers to this ADR,
  and keeps the production-scope upgrade and recovery evidence.
