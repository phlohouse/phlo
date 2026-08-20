# Phlo agent

An [Eve](https://eve.dev)-based engineering agent for the Phlo ecosystem,
inspired by [Evi](https://github.com/hugorcd/evlog/tree/main/apps/evi).

The initial setup includes:

- DeepSeek V4 Flash for text and Qwen 3.7 Flash for turns containing images
- Vercel AI Gateway routing with caching and usage tags
- a GitHub channel and GitHub tools scoped to `phlohouse/phlo`
- grounded classification and enrichment for newly opened GitHub issues
- Agent Browser and the `before-and-after` CLI for visual verification
- an isolated Vercel Sandbox containing a shallow Phlo checkout
- approval-gated GitHub writes through the GitHub Tools extension
- autonomous repository-health and upstream ecosystem maintenance schedules
- guarded feature-branch pushes and draft pull requests from scheduled runs

## Autonomous maintenance

The agent runs one maintenance session at 08:00 UTC every Tuesday and Thursday.
Each session performs both procedures:

| Procedure | Outcome |
| --- | --- |
| Repository health | Grounded documentation, example, test, CI, and convention findings |
| Upstream ecosystem sync | Compatibility-impacting changes across every Phlo provider package and its external packages, APIs, protocols, service images, frontend dependencies, workflows, and the agent's Eve dependencies |

The upstream inventory is rebuilt from all package manifests, lockfiles,
service definitions, workflows, and `registry/support/v1.json` on every run; it
is not a fixed package list. Renovate continues to own routine version and
image-digest bumps. The agent focuses on release-note analysis, API or default
changes, migrations, security notices, test gaps, and other compatibility work
that a version bot cannot infer.

Each run searches existing issues and pull requests first. A mechanical,
low-risk fix may become a verified **draft** pull request. A finding that needs
product judgment may become a focused issue. Runs create nothing when there is
no grounded work, and each skill limits how many artifacts one run can produce.

The schedule identity can push feature branches, create issues, and open draft
pull requests. It cannot push `main` or `master`, open a non-draft PR without
approval, merge, publish, release, or modify secrets. Git credentials are
injected by the sandbox network broker and are never exposed to its processes.

Autonomous writes are disabled by default. Scheduled audits still run, but they
cannot create issues, push branches, or open pull requests until
`PHLO_AGENT_AUTONOMOUS_WRITES=1` is set in the deployed Vercel environment.

## Automatic issue triage

Newly opened issues are checked against current source, documentation, issues,
and pull requests. The agent applies a small set of existing repository labels
and posts one concise comment with genuinely useful evidence or a focused
question. It does not close, assign, rewrite, or implement issues during
automatic triage. Issue bodies are treated as untrusted evidence rather than
agent instructions.

## Post-merge rollout

1. Update a local Phlo checkout and install the agent with Node.js 24:

   ```bash
   git switch main
   git pull --ff-only origin main
   cd apps/phlo-agent
   npm ci
   npm run typecheck
   npm test
   npm run build
   ```

2. In the Vercel team that will own the agent, enable AI Gateway, review its
   available credit, and configure spend alerts. Do not buy credits merely to
   install or build. Add paid credits before the canary only if DeepSeek V4
   Flash is unavailable on the free tier, rate-limited, or the balance is
   exhausted.

3. Provision the `github/phlo-agent` Vercel Connect connector, link the Vercel
   project, and install its GitHub App on `phlohouse/phlo`:

   ```bash
   npx eve add channel/github
   npx eve link
   ```

   Grant repository contents, issues, and pull request write access. Keep the
   connector name `github/phlo-agent`. If the selected GitHub App slug differs
   from `phlo-agent`, set `PHLO_AGENT_GITHUB_BOT` to that slug. Subscribe the
   connector to `issues`, `issue_comment`, and `pull_request_review_comment`.

4. Set `PHLO_AGENT_AUTONOMOUS_WRITES=0` in the Vercel production environment,
   then deploy:

   ```bash
   npx eve deploy
   ```

5. Verify the deployed health endpoint and confirm Vercel discovered the
   `maintenance` cron job with expression `0 8 * * 2,4`.

6. Run the write-disabled canary described below. Review its Agent Run and
   confirm that it reads current `main`, loads both maintenance skills, and
   creates no GitHub artifact.

7. Set `PHLO_AGENT_AUTONOMOUS_WRITES=1` in Vercel production and redeploy.
   Monitor the first Tuesday or Thursday run and review every issue or draft PR
   it creates. To stop writes, restore the value to `0` and redeploy.

## Local setup

Requires Node.js 24 or newer.

```bash
cd apps/phlo-agent
npm ci
cp .env.example .env.local
# Add AI_GATEWAY_API_KEY to .env.local.
npm run dev
```

Installing and type-checking do not call a model. `npm run dev` starts using AI
Gateway credit when you send the first prompt.

`PHLO_AGENT_MODEL` and `PHLO_AGENT_VISION_MODEL` can override the defaults
without changing source.

## Connect GitHub and deploy

The checked-in connector name is `github/phlo-agent`. Provision that connector
and its GitHub App interactively, then install the app on `phlohouse/phlo`:

```bash
cd apps/phlo-agent
npx eve add channel/github
npx eve link
npx eve deploy
```

The generated/provisioned channel must keep the connector name
`github/phlo-agent`. If the available GitHub App name is not `phlo-agent`, set
`PHLO_AGENT_GITHUB_BOT` to the selected app slug in the Vercel project.

The GitHub App installation needs repository contents, issues, and pull request
write access so scheduled runs can push feature branches and deliver proposals.

Deployment and connector provisioning change shared Vercel and GitHub state,
so they are intentionally not performed by repository setup.

## Canary and enable writes

After deploying with autonomous writes disabled, trigger one maintenance run
from a local Eve development session and inspect its Agent Run and logs:

```bash
cd apps/phlo-agent
PHLO_AGENT_AUTONOMOUS_WRITES=0 npm run dev

# In another terminal:
curl -X POST http://localhost:2000/eve/v1/dev/schedules/maintenance
```

The response includes the schedule session ID. Confirm that the run loads both
maintenance skills, reads current `main`, and creates no GitHub artifact. Then
set `PHLO_AGENT_AUTONOMOUS_WRITES=1` in the Vercel project's production
environment and redeploy. Setting it back to `0` and redeploying is the kill
switch.

## Billing

No billing setup is required to install, build, or type-check the agent. A
model credential is required before the first interactive prompt.

Vercel AI Gateway provides a monthly free-credit tier for eligible models.
DeepSeek V4 Flash is billed per token at the provider's list price with no
Gateway markup. Add paid AI Gateway credits when either:

1. the model is not eligible for the free tier on the team at execution time,
2. free-tier rate limits block the workload, or
3. the free balance is exhausted.

Buying credits moves the team to AI Gateway's paid tier and ends its monthly
free-credit allocation. Production also consumes the Vercel resources used by
Eve: Functions, Workflows, and Sandbox. Configure spend alerts before deploying:
the two weekly scheduled runs begin automatically once Vercel enables the cron
job.

Current prices and eligibility can change. Check the
[AI Gateway pricing page](https://vercel.com/docs/ai-gateway/pricing) and the
DeepSeek V4 Flash model page in the Vercel dashboard before setting a budget.

## Deferred capabilities

Linear reporting, long-term memory, telemetry, automatic merging, and releases
are not enabled.
