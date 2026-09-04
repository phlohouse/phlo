# ADR 0050: Freeze the Release Promotion Contract

## Status

Accepted (2026-09-03). Supersedes no prior ADR; there are no ADRs previously committed to this repository. Companion decisions: ADR 0047 (production trust and readiness contract), ADR 0048 (run-evidence semantics), ADR 0049 (continuity and upgrade contract) — all local to the programme register.

**Approval slot — release owner:** the maintainer/release-owner sign-off for this ADR is recorded by the programme controller in the plans ledger (`plans/014-freeze-release-promotion-contract.md`). This ADR is Accepted on that basis. The release-owner approval slot it establishes for *candidate* promotions is defined in the Decision table (concern 6a) and is exercised per release, not per ADR.

## Context

Phase 3 of the phlo-v1 roadmap requires clean-host acceptance of release candidates from immutable artifacts, with repeated evidence gating promotion. Today the repository has the pieces but not the contract that joins them:

- **The nightly journey is not release acceptance.** The release golden path (`scripts/release_golden_path.py`, `golden_path_ci` capability) exercises a blessed workflow from a source checkout. A source-checkout run proves the code works; it cannot prove that the artifacts a consumer installs (PyPI distributions, release container images, pinned provider images) are the artifacts that work.
- **Candidate protection is SHA-scoped, not artifact-scoped.** `release-candidate-protection.md` and `security/release-candidate-ruleset.json` gate a *commit SHA* on CI, integration, security, and the golden path. They say nothing about what artifact set a candidate consists of, how it is staged, or whether promotion rebuilds it.
- **Artifact identity exists but is not bound to acceptance.** `scripts/release_identity.py` computes SHA-256 content digests and emits a publish plan, but no decision says that evidence and promotion must reference those digests.
- **The Plan 013 fixture exists (merged #826).** An immutable supported-pair upgrade fixture (0.14.0 → 0.15.0 with a declared rollback-safe step) is available as an input assumption; nothing defines which demonstrations must run against it before promotion.
- **Downstream work would otherwise invent policy ad hoc.** Plans 015–016 (golden path from an artifact BOM; promotion gated on repeated evidence) and provider trust tiers (#854) each need one frozen answer for what a candidate is, where it lives, and who promotes it.

Issue #720 (release-candidate gate) and #717 (release-candidate generation) closed the *gate* half of this problem. This ADR closes the *promotion contract* half. It is a decision only: no runtime code, no gate flip, and no publication is authorised by it, and `registry/support/v1.json` and `scripts/validate_support_manifest.py` are unchanged.

## Decision

The following contract is frozen. Every concern is decided; the table at the end summarises.

### 1. Candidate artifact set (BOM) and identity model

A **v1 release candidate** is an exact, enumerable artifact set — the **Bill of Materials (BOM)** — consisting of:

| Kind | Inventory |
| --- | --- |
| Source identity | The release commit SHA (the tag target), plus the full source tree it pins, including `uv.lock`, `registry/support/v1.json`, and `registry/plugins.json` as committed at that SHA |
| Python distributions | Exactly one sdist and one wheel for every workspace package published by the release (root `phlo` and every `packages/*` distribution), built once from the release commit |
| First-party container images | The `phlo-api` and Observatory release images built by `build-core-services.yml` from the release commit, referenced by immutable digest |
| Pinned provider images | Every third-party container image referenced by the committed `service.yaml` files and root `defaults`/`core-services` extras at the release commit, referenced by immutable digest |
| Support boundary | `registry/support/v1.json` at the release commit (read-only input to acceptance; never a promotion output) |

**Identity.** Each artifact is identified by its SHA-256 content digest, computed by `release_identity.py` for Python distributions and registry digests for images. The BOM is a JSON document listing every artifact with `{kind, name, version, digest, source}`; the **canonical candidate digest** is the SHA-256 of the canonicalised (keys sorted, whitespace-free) JSON array of artifact digests in BOM order. A candidate is identified by the pair *(release commit SHA, canonical candidate digest)*. Two candidate identities are the same candidate only if both components match; a rebuilt artifact with a different digest is a different candidate, full stop.

### 2. Staging namespace and immutability rules

Candidates are staged in an **immutable staging namespace** before any evidence may be recorded against them:

- Python distributions are stored exactly once as build-workflow artifacts and in a **draft GitHub Release**; their digests are recorded in the BOM at staging time and never recomputed-or-replaced afterwards.
- Container images are pushed once with candidate-scoped, immutable tags (`phlo-v1-candidate-<short-candidate-digest>`, never `latest` or a moving tag) and identified downstream by digest only.
- The staging namespace is **append-only**: nothing is deleted, overwritten, or rebuilt inside it. The only permitted mutation is finalisation (publishing the draft release) at promotion time, which changes visibility, not bytes.
- The BOM document itself is committed to the release evidence store at staging time and is thereafter read-only.

### 3. No-rebuild promotion

**Promotion never rebuilds.** Promotion is a change of visibility and namespace for the exact staged bytes:

- PyPI publication uploads the staged sdist/wheel files and verifies each uploaded digest equals its BOM digest before the publish step reports success.
- Container promotion copies or re-tags images **by digest** into the public namespace (for example `ghcr.io/phlohouse/<name>:<version>` alongside the digest reference); it never re-runs a Dockerfile.
- If any staged artifact is missing, corrupted, or digest-mismatched at promotion time, promotion halts and the candidate is rejected (see concern 8). A fix means staging a **new candidate** with a new canonical digest.

### 4. Evidence bundle

Every candidate accumulates one **evidence bundle**, stored alongside it in the staging namespace and named `release-candidate-evidence-<release-commit-sha>`. The bundle must cover **every Horizon A runtime demonstration** — the complete Phase-3 exit set:

| Demonstration | Source of evidence |
| --- | --- |
| Clean-host golden path from the staged artifacts (Plan 015 runner) | Golden-path runner log + per-step run evidence, executed against installed artifacts, not a source checkout |
| Blessed-workflow run-evidence profile (per ADR 0048) | Run-evidence records from the golden-path execution |
| Supported-pair upgrade and recovery (Plan 013 fixture 0.14.0 → 0.15.0) | `phlo operations upgrade plan` / `apply` evidence, including the rollback-boundary result |
| Verified backup set and restore to explicit target (Plans 011–012) | Recovery-drill output bound to the candidate |
| Security and integration gates on the exact SHA | Constituent CI conclusions (as already aggregated by `release candidate / status`) |
| Support-boundary consistency | `scripts/validate_support_manifest.py` exit 0 at the release commit |

Each bundle record carries: the candidate identity pair, the digest of every artifact actually exercised, the executing environment identity (runner/host, must be a clean host with no source checkout), UTC timestamps, workflow run URLs, and the pass/fail conclusion. **Retention:** evidence bundles are retained for the full support life of the release they gate and never less than 24 months (GitHub artifact retention is extended by archiving the bundle into the draft release before finalisation).

### 5. Qualifying repeated evidence

Promotion is gated on **qualifying runs**, owned and adjudicated by the **release owner**:

- **Count:** at least **three (3)** qualifying runs per candidate. A qualifying run is a complete, successful execution of the full evidence-bundle demonstration set (concern 4) against the staged artifacts, on a clean host, producing no failed demonstration.
- **Distinctness:** the three qualifying runs must execute on **three distinct clean hosts** and at least **two distinct calendar days** (UTC).
- **Freshness:** the newest qualifying run must be no older than **7 days** at authorization time; no qualifying run may predate the staging of the exact canonical digest it references.
- **Binding:** every qualifying run must record the same canonical candidate digest and artifact digests as the BOM. Evidence from any other digest (including an earlier commit's artifacts) is non-qualifying.
- **Retry and failure rules:** infrastructure failures (runner loss, network outage) that occur before the acceptance suite begins producing evidence may be retried; every attempt, successful or not, is appended to the bundle. A functional failure — any Horizon A demonstration failing on the candidate — is terminal for that candidate: it is rejected and a new candidate must be staged. Silent or unrecorded retries are prohibited.
- **Owner:** the release owner schedules qualifying runs, adjudicates each conclusion, and curates the bundle. No qualifying-run evidence may be added by the automation that will later perform promotion.

### 6. Promotion authority and publish ordering

- **Authority.** Promotion to a published channel requires exactly **one recorded release-owner authorization** — a signed approval record naming the candidate identity pair, the evidence bundle digest, and the target channel — attached to the bundle before any publish step runs. The publish workflow refuses to publish without it. (Release-emergency bypass of the branch ruleset under `release-candidate-protection.md` does not authorise artifact promotion; it only unblocks merging/tagging, and any such bypass is recorded in the bundle.)
- **Ordering.** Publication proceeds in this fixed order, and each step is idempotent and digest-verified: (1) the release tag is created on the release commit; (2) Python distributions are published to PyPI; (3) container images are promoted by digest; (4) the draft GitHub Release is finalised, attaching the final BOM and evidence bundle. Nothing is publicly consumable before step 2; the finalisation in step 4 is the announcement point, never the first publication.
- **Post-publication reconciliation.** Within **24 hours** of finalisation, a reconciliation job re-fetches every published artifact (PyPI content hashes, registry image digests, release assets) and verifies each equals its BOM digest; the result is a reconciliation report appended to the evidence bundle. A reconciliation mismatch is a revocation event (concern 8).
- **Partial-publication response.** If publication fails partway, the response is **forward completion**: re-run the failed step only, for the same digests, until every artifact kind is published or the candidate is formally abandoned. Partial publication is recorded in the bundle; the release must never be left silently half-published. If the failure cannot be completed, the release owner revokes the published portions and records the abandonment.

### 7. Support-manifest promotion is explicitly separate

`registry/support/v1.json` records the **support classification** of packages, services, and capabilities (`target_status`, `current_maturity`, gates). Changing it is a **code-review decision on `main`** (support census / explicit support decisions work, roadmap T3-04/T3-05) and is **neither a promotion gate nor a promotion output** under this ADR: release promotion moves immutable artifacts between namespaces; support-manifest changes move classification text through normal review. This ADR flips no gate and no status in that manifest, and `scripts/validate_support_manifest.py` must continue to pass unchanged against it.

### 8. Failure and revocation response

- **Pre-publication rejection** (missing artifact, digest mismatch, functional failure, stale evidence): promotion halts; the candidate and its bundle are retained for audit with a rejection record; the digest is never re-staged under the same identity.
- **Post-publication revocation** (reconciliation mismatch, defect discovered): the release owner issues a revocation record naming the candidate identity pair and reason; the GitHub Release is marked revoked/removed, the PyPI release is yanked, and public image tags pointing at the digests are removed (digests remain pullable for forensics). A revoked canonical digest is never re-promoted; the fix ships as a new candidate.
- **Rollback of published state is prohibited**; consumers are directed to the last non-revoked candidate.

### Decision table

| # | Concern | Decision |
| --- | --- | --- |
| 1a | Candidate artifact set | Source commit + all published Python distributions + first-party release images + pinned provider images + support manifest, enumerated in a committed BOM |
| 1b | Candidate identity | *(release commit SHA, canonical candidate digest)*; canonical digest is SHA-256 over the canonicalised BOM digest array |
| 2 | Staging namespace | Immutable, append-only draft-release + workflow artifacts + candidate-tagged images, identified by digest |
| 3 | Promotion mechanism | Visibility/namespace change of the exact staged bytes; digest-verified uploads; promotion by digest; never rebuild |
| 4 | Evidence bundle | `release-candidate-evidence-<sha>` covering all six Horizon A demonstrations; retained ≥ 24 months |
| 5 | Qualifying evidence | ≥ 3 qualifying runs, 3 distinct clean hosts, ≥ 2 calendar days, newest ≤ 7 days at authorization; infra failures retried and logged, functional failures terminal; owned by the release owner |
| 6a | Promotion authority | One recorded, signed release-owner authorization bound to candidate + bundle + channel; publish workflow fails closed without it |
| 6b | Publish ordering | Tag → PyPI → images by digest → GitHub Release finalisation; fixed, idempotent, digest-verified |
| 6c | Reconciliation | Automated digest re-verification of all published artifacts within 24 h, report appended to the bundle |
| 6d | Partial publication | Forward completion of the same digests only; otherwise recorded revocation/abandonment |
| 7 | Support manifest | Classification changes are normal reviewed code changes on `main`; separate from, and unchanged by, release promotion |
| 8 | Revocation | Named revocation record; yank/unlist/remove tags; digest never reused; rollback prohibited, fix forward |

## Consequences

**Positive.** Plans 015–016 (T3-02, T3-03) inherit a frozen BOM, staging model, and evidence/gating contract instead of inventing one. Provider trust tiers (#854 / T7-01) can bind conformance evidence to a stable candidate identity. Consumers and auditors can reconstruct exactly which bytes were tested and who promoted them. Clean-host acceptance from immutable artifacts — the Phase-3 exit — is now a specified procedure, not an aspiration.

**Costs and follow-through.** The Plan 015/016 implementation work must build the BOM emitter, staging automation, evidence-bundle writer, and qualification tracker this ADR describes; until they exist, promotion continues under the existing SHA-scoped candidate protection, which remains a necessary but not sufficient gate. Qualifying-run scheduling adds up to three days of wall-clock latency to a release. The 24-month evidence retention exceeds default GitHub artifact retention and requires the archival step in concern 4. The release-owner approval slot makes promotion an explicit human act; automation may prepare but never authorise.

**Neutral.** Nothing in this ADR changes runtime code, CI gate status, publication state, or `registry/support/v1.json`; `scripts/validate_support_manifest.py` must continue to pass unchanged.
