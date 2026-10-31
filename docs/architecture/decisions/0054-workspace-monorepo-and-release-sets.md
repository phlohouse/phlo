# ADR 0054: Workspace monorepo released as one release set

## Status

**Accepted (back-filled 2026-10-30)**

Amended (2026-10-31): decision 2 applies to releases below `1.0.0`. From `1.0.0`, every distribution in the release set shares one version under [ADR 0053](0053-what-1-0-promises.md) decision 4, resolved by `scripts/release_lockstep_version.py` and checked by `scripts/validate_support_manifest.py` ([#1231](https://github.com/phlohouse/phlo/pull/1231)). Each `pyproject.toml` remains its distribution's only version authority.

- Original decision: March 2026 (ReleaseX adoption), extended August–October 2026
- Evidence: [#300](https://github.com/phlohouse/phlo/pull/300) (2026-03-26),
  commit `8f8247e2` (2026-03-28, "adopt ReleaseX release_set mode"),
  [#732](https://github.com/phlohouse/phlo/pull/732) (2026-08-20),
  [#895](https://github.com/phlohouse/phlo/pull/895) (2026-09-04),
  [#914](https://github.com/phlohouse/phlo/pull/914) (2026-09-06),
  [#1102](https://github.com/phlohouse/phlo/pull/1102) (2026-10-10)
- Decision owner: Phlo project maintainers

## Context

Phlo ships one core distribution, `phlo`, from the repository root and 39
provider and service distributions from `packages/*`. They live in one Git
repository and one uv workspace (`[tool.uv.workspace] members = [".", "packages/*"]`
in the root [pyproject.toml](../../../pyproject.toml)), share one `uv.lock`, and
resolve each other through `[tool.uv.sources]` workspace entries.

Until March 2026 releases used release-please with a custom
`.github/scripts/publish_plan.py`. [#300](https://github.com/phlohouse/phlo/pull/300)
replaced it with ReleaseX and gave these reasons: auto-discovery of uv workspace
members, independent per-package versioning, dependency-driven cascade bumps,
and one `relx.toml` instead of a configuration file, a manifest and a publish
script. #300 configured `release_mode = "unified"`. Two days later commit
`8f8247e2` switched to `release_mode = "release_set"` and disabled cascade
bumps; its message records no rationale, and none was found in issues.
Cascade bumps were re-enabled in [#914](https://github.com/phlohouse/phlo/pull/914).

The packages are not versioned in lockstep. At `origin/main` `7feced4d`, the
root is 0.18.0 and the 39 workspace packages range from 0.1.0 to 0.18.0.

## Decision

1. **One workspace, one lock.** Core and every first-party provider are
   members of one uv workspace and are developed, tested and locked together.
2. **Each distribution owns its version.** A distribution's `pyproject.toml` is
   its only version authority. Sources carry no `__version__` literals and the
   plugin registries carry no version column
   ([#895](https://github.com/phlohouse/phlo/pull/895));
   `scripts/check_version_drift.py` fails on drift.
3. **ReleaseX computes versions from conventional commits.** `relx.toml` sets
   `strategy = "conventional_commits"`. Changelog sections map `feat` to Added,
   `fix` to Fixed and `refactor`/`perf` to Changed; `docs`, `chore`, `ci`,
   `build`, `style` and `test` are excluded.
4. **One release PR versions a release set.** `[monorepo] release_mode =
   "release_set"` produces one in-repository PR (`chore(release): …` on a
   `relx/release/` branch) that bumps every selected package. The changelog
   heads each release as "phlo X + N packages".
5. **Core minor versions are compatibility epochs.** With `cascade_bumps = true`,
   selecting `phlo` rewrites every `packages/*` dependent to
   `phlo>={version},<{next_minor}`. The same rule applies to the internal
   dependencies listed under `[workspace.dependencies]`. `scripts/validate_support_manifest.py`
   requires every provider to declare exactly `phlo>=X.Y.Z,<X.(Y+1)` for the
   current core version.
6. **ReleaseX prepares; it does not publish.** `[publish] enabled = false`
   ([#1102](https://github.com/phlohouse/phlo/pull/1102)). The release PR also
   runs the support-matrix and example-lock transformers and the checked
   replacements for the support manifest, first-party image tags and example
   pins ([#732](https://github.com/phlohouse/phlo/pull/732)). Publication
   belongs to the promotion workflow defined by
   [ADR 0050](0050-freeze-release-promotion-contract.md).
7. **Two channels.** `main` produces stable versions; `beta` produces `b`
   pre-releases.

## Consequences

- One human merge of the release PR versions the whole set; ADR 0050 makes that
  merge the publication authority.
- A core minor release re-versions nearly every provider, because each provider's
  core range changes. The 0.18.0 release PR
  ([#970](https://github.com/phlohouse/phlo/pull/970)) covered phlo plus 38 packages.
- Equal version numbers do not imply a tested combination. The support manifest's
  `release_set` ([ADR 0058](0058-support-manifest-as-support-authority.md))
  records the exact tested blessed-core versions.
- Release-note quality and bump size depend on conventional squash-merge titles.
  No commit or PR-title lint enforces them; ReleaseX parses whatever lands.
- Providers cannot be released against an older core minor without a release set
  that rewrites their range.

## Alternatives Considered

- **release-please with a custom publish plan:** replaced by #300 because it
  required three configuration files and a bespoke script for the monorepo.
- **ReleaseX `unified` mode:** used for two days; replaced by `release_set`.
  The reason for the switch is not recoverable from the repository or issues.
- **Lockstep versioning of every distribution:** not adopted below `1.0.0`.
  Packages keep independent versions, and the core-minor range carries
  compatibility instead. ADR 0053 decision 4 adopts it from `1.0.0`.
- **Publishing from ReleaseX:** disabled in favour of the build-once promotion
  path in ADR 0050.

## Related

- [ADR 0050](0050-freeze-release-promotion-contract.md): release promotion contract.
- [ADR 0058](0058-support-manifest-as-support-authority.md): support manifest and release set.
- [relx.toml](../../../relx.toml), [scripts/check_version_drift.py](../../../scripts/check_version_drift.py).
- [Qualify a trusted-publishing release](../../contributing/release-publication.md).
