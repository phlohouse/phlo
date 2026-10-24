# Phlo agent skills

These repository-local skills guide agents through Phlo-specific workflows.
Invoke a skill by name, or let an agent select it from its description.
Run commands from the repository root unless the skill names another directory.

| Skill | Use it for |
| --- | --- |
| [phlo-testing-observatory](phlo-testing-observatory/SKILL.md) | Browser behaviour, visual evidence, and real-lakehouse acceptance |
| [phlo-assessing-plugin-impact](phlo-assessing-plugin-impact/SKILL.md) | Core and provider contract changes, deprecations, and removals |
| [phlo-accepting-packages](phlo-accepting-packages/SKILL.md) | Installed-wheel, generated-project, and staged-candidate acceptance |
| [phlo-verifying-releases](phlo-verifying-releases/SKILL.md) | Read-only verification of qualification and published artifacts |
| [phlo-measuring-performance](phlo-measuring-performance/SKILL.md) | Reproducible latency, work-count, and memory comparisons |
| [phlo-refactoring-docs](phlo-refactoring-docs/SKILL.md) | Source-verified documentation rewrites without losing facts |

The [contribution guide](../../CONTRIBUTING.md),
[engineering map](../../docs/contributing/engineering-map.md), and
[verification matrix](../../docs/contributing/verification-matrix.md) remain
authoritative. Skills select and apply those contracts, rather than replace them.
Loading a skill grants no permission to publish, deploy, change shared settings,
or mutate an existing deployment.

## Inspiration

These are Phlo-specific adaptations of workflow ideas from
[OpenClaw's skills](https://github.com/openclaw/openclaw/tree/main/.agents/skills):
`control-ui-e2e`, `plugin-sdk-impact`, `release-openclaw-plugin-testing`,
`verify-release`, `openclaw-performance`, and `openclaw-refactor-docs`, respectively.
They reuse Phlo's own scripts and contracts, without OpenClaw's runtime,
maintainer permissions, remote runners, or publication machinery.
