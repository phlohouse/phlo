---
name: phlo-verifying-releases
description: "Verifies an exact Phlo release: its smoke run, public package hashes, attestations and image signatures. Use when asked whether a version is released or qualified; does not publish."
---

# Verify a release

Inspect current release state without changing it. Read the
[release procedure](../../../RELEASING.md) and
[release publishing ADR](../../../docs/architecture/decisions/0068-release-publishing.md).
Those sources own publisher identities, qualification and recovery rules.

## 1. Resolve the exact release

Resolve the requested version or tag and its commit. Read live GitHub state:

```bash
gh release view <tag> --repo phlohouse/phlo --json tagName,isDraft,isPrerelease,publishedAt,url,assets,body
gh run list --repo phlohouse/phlo --workflow release-promotion.yml --limit 20 --json databaseId,headSha,status,conclusion,url
```

Treat the run list as discovery, not proof. Find the Release run whose head SHA
is the tag's commit. Use metadata from the release's exact tag and BOM rather
than a divergent local checkout. Finish when the release identity and its run
are unambiguous, or report the missing identity as a blocker.

## 2. Check the smoke and the publish job

In that run, confirm the amd64 artifact golden path and the publish job
succeeded, and note the arm64 result. Download its `smoke-evidence-*` artifact
and validate it against the release BOM:

```bash
uv run --locked python scripts/validate_release_evidence.py --bundle <evidence.json> --candidate-bom <bom.json>
```

Distinguish artifact qualification from production qualification using the
BOM's embedded support manifest. Publication does not upgrade its
production-readiness claim. Smoke evidence is kept with the run, not archived,
so an old release may only have its attestations and signatures.

## 3. Reconcile public artifacts

Derive expected distributions and images from the exact BOM. Query PyPI's
version JSON for every expected distribution and compare filenames and SHA-256
values with the BOM. Check distribution attestations with
`gh attestation verify <file> --repo phlohouse/phlo`. Compare public container
digests with the BOM and verify their signatures using the exact workflow
certificate identity and issuer in the release procedure. Report missing
artifacts and mismatches individually.

For a harmless published CLI smoke check, install the exact published version
in a disposable environment outside the workspace. Run `phlo --version` and
`phlo --help` using that environment's executable. Remove source overrides and
confirm imports resolve from the installation. Do not start services, mutate
an existing project, dispatch workflows or publish.

## 4. State the verdict

Report separately whether the release is published, artifact-qualified, and
production-qualified. Include the exact version, release and run URLs, public
reconciliation results, smoke outcome, and remaining gaps. Missing access means
"not verified", not a confirmed publication failure. Finish with the
evidence-backed verdict, not a claim based only on green CI or the presence of
a GitHub release page.
