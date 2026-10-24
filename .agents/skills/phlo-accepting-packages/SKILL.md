---
name: phlo-accepting-packages
description: "Validates installed Phlo wheels, provider discovery, generated projects, and staged candidates. Use for packaging changes or pre-release artifact acceptance."
---

# Accept installed packages

Select source-built package validation or immutable candidate qualification
before running commands. Read the package and integration rows of the
[verification matrix](../../../docs/contributing/verification-matrix.md).

## 1. Bind the inputs

For development validation, record the checkout revision, local modifications,
package inventory, and build outputs. For a staged candidate, record its exact
candidate SHA, staging run, BOM digest, and previous candidate identity.
Use the release set in `registry/support/v1.json` for current-source work and
the authenticated BOM's embedded support manifest for candidate requirements.

Read the [release publication procedure](../../../docs/contributing/release-publication.md)
before candidate acceptance. It owns qualification rules, repeated-run evidence,
and recovery. Dispatching staging, acceptance, or promotion is not authorised
merely by loading this skill. Request explicit permission for remote workflow
dispatch or writes to shared infrastructure.

## 2. Validate development wheels outside the workspace

Inspect `scripts/verify_installed_provider_artifacts.py`, then run:

```bash
uv run --locked python scripts/verify_installed_provider_artifacts.py --help
```

The verifier builds workspace wheels unless `--wheelhouse` supplies wheels from
the exact run. It installs an external consumer, removes source-import overrides,
and checks installed providers and generated service configuration. Run it with
`--output <report.json>` in an owned scratch directory. Keep Docker builds and
health checks opt-in through `--include-docker-builds` and
`--include-docker-health`; inspect resource use and cleanup first.

For release-set dependency or extras changes, run
`scripts/check_published_extras.py --distributions <wheelhouse>` against the
built release set. This checks resolution without workspace source redirects;
it does not prove that those wheels are already published.

Check the changed package's entry points, bundled manifests or templates,
discovery, and generated project through the installed CLI. Use a fresh
consumer directory and verify imported module paths. An editable installation
or source bind mount cannot establish wheel contents.

## 3. Accept a staged candidate without rebuilding

Reuse the authenticated producer and exact artifact bytes. The existing
`.github/workflows/release-artifact-acceptance.yml` owns the hosted path.
For authorised local execution, inspect the golden-path driver and run:

```bash
uv run --locked python scripts/release_golden_path.py --help
```

Supply `--candidate-bom <bom.json>`,
`--previous-candidate-bom <previous-bom.json>`, and
`--evidence-output <evidence.json>`. The staged distributions must remain beside
their BOM as expected by the driver. Keep all projects, volumes, identities, and
credentials isolated from existing deployments.

Validate the resulting bundle with:

```bash
uv run --locked python scripts/validate_release_evidence.py --bundle <evidence.json> --candidate-bom <bom.json>
```

Let the BOM and canonical validators determine artifact versus production
qualification. Keep unsupported upgrades, unavailable prerequisites, and failed
attempts visible. Never relabel a backup, substitute rebuilt wheels, or weaken
the required checks to obtain passing evidence.

## 4. Report the supported conclusion

Record input identities, commands, architecture, installed import provenance,
passed boundaries, failures, and omitted checks. Separate development wheel
validation, staged-artifact acceptance, and production qualification. A single
local success does not satisfy repeated-run release requirements. For public
publication checks, continue with
[release verification](../phlo-verifying-releases/SKILL.md).
