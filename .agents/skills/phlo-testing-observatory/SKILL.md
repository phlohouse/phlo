---
name: phlo-testing-observatory
description: "Tests Phlo Observatory in the browser and records visual or real-lakehouse evidence. Use for UI changes, user journeys, and Observatory acceptance."
---

# Test Observatory

Prove the affected user journey at its actual boundary. Read the Observatory
rows of the [verification matrix](../../../docs/contributing/verification-matrix.md)
and locate the owning route, component, or adapter with the
[engineering map](../../../docs/contributing/engineering-map.md).

## 1. Select the evidence

Record the checkout revision, affected journey, expected results, and test mode:

- Component or fixture-backed browser checks prove rendering and interaction,
  not provider connectivity or persistence.
- A source-built, disposable lakehouse proves the current source integration.
- Staged-artifact acceptance proves the exact installed candidate. Use
  [package acceptance](../phlo-accepting-packages/SKILL.md) for that mode.

Include the affected loading, empty, populated, error, and permission states.
For environment or branch changes, include a custom environment and primary ref
with distinguishable data. Read the
[independent-lakehouse acceptance record](../../../docs/contributing/observatory-independent-lakehouse.md)
for the assumptions those cases exposed. Finish selection when every changed
behaviour has an expected outcome and an identified evidence boundary.

## 2. Prepare the intended runtime

Run `make setup` before repository-wide checks. For frontend commands, use
`packages/phlo-observatory/src/phlo_observatory` and inspect its `package.json`.
Use existing tests and fixtures before introducing another server or driver.

For a disposable source-built journey, inspect
`scripts/observatory_demo.py` and run:

```bash
uv run --locked python scripts/observatory_demo.py --help
```

That driver requires Docker, a compatible Linux GNU native transformer binary,
its SHA-256, and explicit `--run-disposable`. Read its setup and cleanup before
running it. It builds images and writes fixture data in its own project.
`tests/integration/acceptance_1035_browser.mjs` is a specialised real-provider
scenario with fixed ports and companion state, not a generic browser launcher.
Use it only with its matching setup and exclusive ownership of those resources.

Record the actual app URL, API target, environment, and execution source.
Confirm those identities before interacting. Use a disposable project for writes;
obtain explicit permission before changing an existing deployment. Keep private
credentials out of captures, command output, and committed fixtures.

## 3. Exercise and inspect

Use the installed `agent-browser` and its `--help` for browser commands. Act
through visible controls. Check rendered results and the relevant API or stored
outcome independently. A success notification alone does not prove persistence.
For a regression, reproduce the same scenario before and after the fix where
practical, and keep failed attempts distinct from successful retries.

For appearance changes, capture desktop and narrow-width affected states,
then inspect each screenshot. Check overflow, focus visibility, and transient
states. For interaction-only changes, inspect the DOM or accessibility output
and assert the resulting behaviour. Run the focused frontend tests and checks
selected by the verification matrix, including the production build.

## 4. Deliver bounded evidence

Before writing under `.amp/in/`, ensure `/.amp/in/` appears in the repository-local
exclude file located by `git rev-parse --git-path info/exclude`. Preserve existing
rules. Store sanitised captures and reports under a unique
`.amp/in/artifacts/observatory-<scope>/` directory.

Report the revision, runtime mode, commands, observed outcomes, and untested
states. Embed inspected screenshots for visual changes. Label fixture evidence
as fixture evidence. Clean up only resources owned by this run, and leave a
preview server running when the user is reviewing it.
