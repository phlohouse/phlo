# Change, ownership, and release policy

## Status

Adopted by the maintainer on 30 October 2026 for
[#1015](https://github.com/phlohouse/phlo/issues/1015).

Phlo has one maintainer, `@iamgp`, who is also its only human developer. Much
of the work is done by coding agents acting on the maintainer's instructions.
This policy describes that model as it is. It does not assume a second
reviewer exists.

These controls are unchanged and remain in force:

- [CONTRIBUTING.md](../../CONTRIBUTING.md)
  requires behavioural tests and discussion of substantial changes.
- [ADR 0050](../architecture/decisions/0050-freeze-release-promotion-contract.md)
  requires release-owner authorisation for an exact candidate and its
  evidence.
- The [publication procedure](release-publication.md)
  defines manual dispatch, `execute=false` by default, qualification, and
  separate approval for shared settings.

## Material changes

A change is material if it changes any of the following:

- authentication or authorisation
- credential handling
- release or security controls
- migrations
- storage writes or deletion
- recovery guarantees
- public contracts
- package compatibility

A cross-package behavioural change is also material. Classify by risk, not by
line count. In a large mixed change, identify the material parts separately
from the mechanical edits.

## Approval

The maintainer approves the work, whether by opening the PR or by instructing
an agent to do it. Approval given to an agent outside GitHub counts. It does
not need to be repeated in the PR.

An agent may merge only work the maintainer has asked it to merge. Approval to
do the work does not cover publication, deployment, releases, or
shared-settings changes. Those need their own maintainer instruction.

Automated reviews, such as the `phlo-agent` PR review, supply evidence. They
are not an independent accountable reviewer. Treat their blocking findings as
defects to fix or explicitly dismiss before merge.

## Pull request record

Use the [PR template](../../.github/PULL_REQUEST_TEMPLATE.md).
For material changes, record:

- The goal, the non-goals, the affected consumers, and a map separating
  mechanical from behavioural changes.
- Commands and outcomes for the exact revision, known exceptions, and what was
  not verified. Use an observable result that tells the intended behaviour apart
  from a plausible regression, and keep fixture evidence separate from
  operational proof.
- For a fix, the regression test that fails before the fix and passes after
  it, or a specific reason why there is none.
  [#998](https://github.com/phlohouse/phlo/issues/998) owns the CI enforcement.
- A rollback or forward-recovery procedure, its prerequisites, the data-loss
  boundaries, and the check that verifies recovery. A source revert does not
  undo a publication or an irreversible migration. ADR 0050's
  forward-completion and revocation rules govern release recovery.

The squash commit body for a material change keeps the reason, the issue and
PR references, the evidence, and any exceptions. The PR remains the detailed
record. No CI gate checks commit messages.

## Ownership

[CODEOWNERS](../../.github/CODEOWNERS)
groups the maintenance domains, and every entry names `@iamgp`. It routes
review requests and requires no approval. The
[engineering map](engineering-map.md)
identifies authoritative source files and generated boundaries.

There is no second owner for release or security surfaces. Revisit this
policy if another contributor with write access joins.

## GitHub enforcement

The `PR validation and merge queue` ruleset covers `main` and `beta`. It
requires resolved review threads, `pr / required`, and the squash merge queue,
with no bypass actors. It requires zero approvals. The tracked copy in
[`security/release-candidate-ruleset.json`](../../security/release-candidate-ruleset.json)
matches these review settings.

Keep it that way. GitHub does not let an author approve their own PR, so with
one maintainer, a required approval or code-owner review would block every
merge. No `pypi` or `release` environment change is part of this policy.

## Release compatibility

The exact package versions, schemas, and service digests in
`registry/support/v1.json` and the candidate BOM form one lockstep
qualification unit. Support claims apply to that tested combination, not to
arbitrary mixtures. ReleaseX `release_set` selection and cascading
`>=version,<next_minor` dependency updates in
[relx.toml](../../relx.toml) stay as they
are.

Lockstep qualification does not mean equal version numbers or republishing
every package on every core patch. Packages keep their own versions. The MinIO
image is versioned and published on its own, as the publication procedure
describes. Independently qualified provider releases would need a separate
decision and compatibility evidence.
