---
name: phlo-verifying-releases
description: "Verifies exact Phlo release qualification, publication receipts, public package hashes, and image signatures. Use when asked whether a version is released or qualified; does not publish."
---

# Verify a release

Inspect current release evidence without changing publication state. Read the
[release publication procedure](../../../docs/contributing/release-publication.md)
and [promotion contract](../../../docs/architecture/decisions/0050-freeze-release-promotion-contract.md).
Those sources own publisher identities, qualification, and recovery rules.

## 1. Resolve the exact release

Resolve the requested version or tag and its commit. Read live GitHub state:

```bash
gh release view <tag> --repo phlohouse/phlo --json tagName,isDraft,isPrerelease,publishedAt,url,assets,body
gh run list --repo phlohouse/phlo --workflow release-promotion.yml --limit 20 --json databaseId,headSha,status,conclusion,url
```

Treat the run list as discovery, not proof. Follow the exact release assets and
receipt to their candidate, staging run, workflow run attempt, and promotion.
Expand discovery if the relevant run is not in the initial page. Use metadata
from the release's exact tag and BOM rather than a divergent local checkout.
Finish when the release identity and evidence producers are unambiguous, or
report the missing identity as a blocker.

## 2. Authenticate qualification and promotion

Download the release's qualification archive, BOM, provenance, and receipt into
an owned scratch directory. Read the canonical archive validation in
`scripts/promote_release_candidate.py` and evidence validation in
`scripts/release_evidence.py` before trusting their contents. Inspect archives
without extracting untrusted paths. A downloaded checksum from the same mutable
source alone is not independent provenance.

Use `scripts/release_provenance.py collect --help` to inspect authenticated
producer collection. The `collect` path downloads evidence; the `authorize`
path is not part of this read-only workflow. Validate individual bundles with
`scripts/validate_release_evidence.py` and assess the complete evidence set with:

```bash
uv run --locked python scripts/release_qualification_status.py --candidate-bom <bom.json> --evidence-dir <evidence-directory> --json
```

Supply authenticated staging time and prior receipts when required by the
publication procedure. Record missing, stale, failed, or replayed evidence.
Check the promotion receipt for `status=promoted`, `success=true`, and matched
reconciliation. Distinguish artifact acceptance from production qualification
using the candidate's embedded support manifest. Publication does not upgrade
its production-readiness claim.

## 3. Reconcile public artifacts

Derive expected distributions and images from the exact BOM, including deferred
or independently versioned artifacts. Query PyPI's version JSON for every
expected distribution and compare filenames and SHA-256 values with staged
artifacts. Compare public container digests and verify their signatures using
the exact workflow certificate identity and issuer specified in the publication
procedure. Report missing artifacts and mismatches individually.

For a harmless published CLI smoke check, install the exact published version
in a disposable environment outside the workspace. Run `phlo --version` and
`phlo --help` using that environment's executable. Remove source overrides and
confirm imports resolve from the installation. Do not start services, mutate
an existing project, dispatch workflows, publish, or retry promotion.

## 4. State the verdict

Report separately whether the release is published, artifact-qualified, and
production-qualified. Include the exact version, candidate identity, release
and run URLs, public reconciliation results, smoke outcome, and remaining gaps.
Missing access means "not verified", not a confirmed publication failure.
Finish with the evidence-backed verdict, not a claim based only on green CI or
the presence of a GitHub release page.
