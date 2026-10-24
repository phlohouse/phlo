---
name: phlo-assessing-plugin-impact
description: "Assesses Phlo plugin consumers and compatibility risks. Use before changing, deprecating, or removing core exports, provider contracts, entry points, or service manifests."
---

# Assess plugin impact

Determine which consumers depend on the contract before deciding how to change
it. Use the [engineering map](../../../docs/contributing/engineering-map.md)
to distinguish the authority from generated registry and support copies.

## 1. Define the changed contract

Read its definition, direct callers, relevant tests, and history. Record the
current observable behaviour and proposed difference. Include public re-exports,
decorators, discovery, hook dispatch, capability resolution, and serialised
metadata when they expose the same contract under another name.

Start with `src/phlo/plugins/`, the affected provider packages, and their
`pyproject.toml` entry points. Check `registry/plugins.json` and
`registry/support/v1.json` for distribution and support scope. Finish when the
public names and runtime discovery paths are accounted for, not merely the
first matching symbol.

## 2. Inventory consumers and gaps

Search the relevant `src/phlo/`, `packages/`, `tests/plugins/`, and example
projects for each canonical name and alias. Parse package metadata for declared
dependencies and entry points. Follow dynamic registration and manifest readers
that text references cannot establish. Record each consumer's path, distribution,
dependency on the behaviour, and applicable contract test.

Separate workspace source consumers, installed artifacts, and external plugins.
An absent source reference does not establish that published or private plugins
are unaffected. State the revision and search scope. Mark failed scans,
unavailable external inventories, and unresolved dynamic paths as unknown.

For removals, check `tests/plugins/test_deprecation_release.py` and the owning
deprecation implementation before proposing a migration. Preserve the shipped
compatibility contract unless the task explicitly authorises changing it.

## 3. Prove representative compatibility

Choose consumers that distinguish the old and proposed behaviour, including a
provider reached through discovery rather than only direct imports. Run the
owning contract tests and affected package tests using the
[verification matrix](../../../docs/contributing/verification-matrix.md).
Useful focused suites, when they own the changed contract, include:

```bash
uv run --locked pytest tests/plugins/test_public_api_exports.py tests/plugins/test_plugin_entry_points.py tests/tooling/test_provider_core_compatibility.py
```

For packaging, bundled resources, or entry-point changes, use
[installed-package acceptance](../phlo-accepting-packages/SKILL.md).
Select broader checks with `python3 scripts/check_changed.py --dry-run` after
bootstrap. A static import scan cannot prove runtime compatibility.

If registry or support metadata changes, update the authority and use its owning
generator or validator from the engineering map. Run:

```bash
python3 scripts/generate_plugin_registry_data.py --check
python3 scripts/validate_support_manifest.py
python3 scripts/check_version_drift.py
```

## 4. Report the decision evidence

List observed consumers, representative checks, proposed migration, and coverage
gaps. Say "no references detected in the searched workspace scope" when that is
all the evidence supports. A zero with incomplete coverage means impact is
unknown, not that removal is safe. Do not dismiss a security defect because its
affected API has no observed consumers. Finish with the compatibility decision
or the specific unresolved contract requiring maintainer judgement.
