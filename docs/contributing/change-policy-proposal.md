# Proposed change, ownership, and release policy

## Status and existing policy

Proposed for [#1015](https://github.com/phlohouse/phlo/issues/1015), not accepted.
Merging documentation does not resolve the independent-ownership requirement
or authorise a settings change. The maintainer must record adoption or revisions
in the issue before these proposed expectations become binding policy.

[CONTRIBUTING.md](https://github.com/phlohouse/phlo/blob/main/CONTRIBUTING.md) already requires discussion of substantial
changes and behavioural tests. [ADR 0050](https://github.com/phlohouse/phlo/blob/main/docs/architecture/decisions/0050-freeze-release-promotion-contract.md)
already requires one recorded release-owner authorisation for an exact candidate
and its evidence. The [publication procedure](https://github.com/phlohouse/phlo/blob/main/docs/contributing/release-publication.md) documents
manual dispatch, `execute=false` by default, qualification, and separate approval
for shared settings. This proposal does not replace those release controls.

The historical review percentages and contributor counts in #1015 are not a
current audit. They are not used as thresholds here. A read-only GitHub check on
11 October 2026 identified `iamgp` as a repository administrator and the only
human in the contributor listing. That supports routing to `@iamgp`, not a claim
that no other eligible maintainer exists.

The same read-only check found the active `PR validation and merge queue`
ruleset covering `main` and `beta`. Its pull-request rule requires zero
approvals, disables code-owner and latest-push approval, and already dismisses
stale reviews. It requires resolved review threads, `pr / required`, and the
squash merge queue, with no bypass actors. Legacy branch-protection inspection
returned HTTP 403, so this is not an exhaustive audit of inherited settings.

## Proposed material-change expectations

A change is material if it changes authentication or authorisation, credential
handling, release or security controls, migrations, storage writes or deletion,
recovery guarantees, public contracts, or package compatibility. Also treat a
cross-package behavioural change as material. Use risk, not a line-count cutoff.
For a large mixed change, identify the material parts separately from mechanical
edits. If classification is uncertain, ask the maintainer in the PR.

For material changes, propose a recorded human decision from `@iamgp` before
merge. The decision identifies the reviewed revision, rationale, evidence,
exceptions, and recovery limits. If the maintainer authored the change, record
that fact and the lack of independent review. Automated analysis can supply
evidence but is not a second accountable human reviewer. This is an interim
single-maintainer proposal and does not meet #1015's independent-review goal.

Use the [single PR template](https://github.com/phlohouse/phlo/blob/main/.github/PULL_REQUEST_TEMPLATE.md) to record:

- The goal, non-goals, affected consumers, and a mechanical-versus-behavioural
  change map.
- Reproducible commands and outcomes tied to the revision, known exceptions,
  and missing verification. Use an observable result that distinguishes the
  intended behaviour from a plausible regression, not the implementation as
  its own oracle. Identify fixture evidence separately from operational proof.
- For fixes, the regression test that fails before and passes after the fix,
  or a specific justification for its absence. [#998](https://github.com/phlohouse/phlo/issues/998)
  owns executable test and CI enforcement, not a second template.
- A rollback or forward-recovery procedure, prerequisites, data-loss boundaries,
  and the check that verifies recovery. Do not claim a source revert undoes
  publication or an irreversible migration. Release recovery remains subject
  to ADR 0050's forward-completion and revocation rules.

For a material squash merge, propose a commit body that preserves the reason,
issue and PR references, evidence, exceptions, and recovery decision. The PR
remains the detailed record. This proposal adds no commit-message CI gate.

## Proposed ownership routing

[CODEOWNERS](https://github.com/phlohouse/phlo/blob/main/.github/CODEOWNERS) groups core, API and Observatory,
orchestration, storage and catalog, ingestion and quality, release and CI,
security, and documentation. The wildcard routes other packages and files to
the same existing maintainer. The [engineering map](https://github.com/phlohouse/phlo/blob/main/docs/contributing/engineering-map.md) still
identifies authoritative source files and generated boundaries.

All entries name `@iamgp`. They request routing, not independent approval.
No independent fallback or second release/security owner is established by the
evidence above. Those appointments remain unresolved. Do not substitute a bot,
the author, or another entry for the same person as independent ownership.
Confirm willingness and repository write access before adding another owner.

## Proposed lockstep compatibility and release decision

Retain one lockstep **qualification unit** for the exact package versions,
schemas, and service digests in `registry/support/v1.json` and the candidate BOM.
Support claims apply to that tested combination, not arbitrary mixtures. Keep
the current ReleaseX `release_set` selection and cascading dependency-range
updates in [relx.toml](https://github.com/phlohouse/phlo/blob/main/relx.toml). Do not introduce an independent provider
release pipeline in this proposal.

Lockstep qualification does not mean equal version numbers or republishing
every workspace package on every core patch. The current manifest has different
versions, including `phlo` at `0.17.0`, `phlo-minio` at `0.16.1`, and
`phlo-observe-plugin` at `0.2.0`. ReleaseX updates selected dependencies to
`>=version,<next_minor`. The independently versioned MinIO image publication
remains separate, as documented in the publication procedure.

The alternative is independently qualified provider releases with tested core
ranges. That needs a separate maintainer decision and compatibility evidence.
This proposal changes no versions, dependency ranges, manifest, support status,
publication workflow, or production-readiness claim.

## Decisions still required from the maintainer

1. Adopt or revise the risk-based material-change criteria, recorded human
   decision, recovery evidence, and material squash-body expectations.
2. Confirm `@iamgp` as the routing owner. Decide whether the interim
   single-maintainer model is acceptable, or appoint eligible independent
   reviewers and a fallback, including a second release/security owner.
3. Adopt or revise lockstep qualification of the exact manifest/BOM combination
   while retaining current per-package versions and release-set selection.
4. Decide whether any additional GitHub enforcement is appropriate. No mandatory
   independent approval is presumed, and no live setting is changed here.

If independent owners are appointed and the maintainer separately authorises
enforcement, an optional settings change is to update the active ruleset's
pull-request parameters to `required_approving_review_count=1`,
`require_code_owner_review=true`, and `require_last_push_approval=true`.
Leave stale-review dismissal, status checks, merge queue, and bypass actors
unchanged. The existing ruleset covers both `main` and `beta`. Confirm that scope
or approve a separate `main`-only rule before applying the change.
CODEOWNERS alone cannot guarantee independence, and an author cannot approve
their own PR. Do not enable this option for the current single-owner map.

No `pypi` or `release` environment change is proposed. Existing manual release
authorisation remains in force. Any future environment reviewer restriction
needs its own approved identities and settings change. Keep #1015 open until
the maintainer resolves its ownership and enforcement acceptance criteria.
