# Phlo GitHub writer

This server is the trusted write boundary for Phlo's Amp automation. The Amp
project plugin sends narrow requests to the server. The server validates each
request, mints a short-lived installation token for the existing `phlo-agent`
GitHub App, and writes only to `phlohouse/phlo`.

The GitHub App private key belongs in Cloudflare Worker secrets. Do not add the
key to the Phlo Amp project, the repository, or an orb environment.

## Supported writes

The server exposes these routes:

- `GET /health` returns the service status.
- `POST /v1/github-comments` checks the pull request head, validates issue
  triage labels, deduplicates the GitHub delivery, and creates one comment.
- `POST /v1/pull-request-metadata` updates only the title or description of a
  target-bound pull request.
- `POST /v1/pull-request-head` resolves the current head for an authorized
  `@phlo-agent` request before the plugin signs its thread capability.
- `POST /v1/issues` creates one maintenance issue.
- `POST /v1/draft-pull-requests` creates an `agent/*` branch from the supplied
  `main` SHA and opens a draft pull request. Send `pullNumber` instead of
  `baseSha` to append a commit to the existing open phlo-agent draft request and
  refresh its title and body. It refuses changes under `.github/`, `.agents/`,
  `.amp/`, and `.git/`. Its JSON request budget is 20,000,000 characters to fit
  root and example locks in one PR. Other routes retain the 2,500,000-character
  budget. The 50-file and 60,000-character PR-body limits still apply.

All write routes require `Authorization: Bearer <PHLO_GITHUB_PUBLISH_TOKEN>`.
The GitHub App key never leaves the server.

The webhook plugin keeps one private review thread per pull request or issue and
reuses it for later accepted events. In a pull request review thread, ask
phlo-agent directly to update that pull request's title or description. On
GitHub, an owner, member, or collaborator can invoke the same thread by starting
a new issue, pull request, or review comment with `@phlo-agent`.

Use `phlo-automation-host` for the persistent PR and issue host, not
`phlo-review`. The host can inspect and refresh its checkout, reload plugins,
and report to other Amp threads. Webhook-created children still use
`phlo-review`, which has no shell, reload, or coordination tools.

Set the global Amp plugin configuration key `phloGitHubAutomationHost` to the
thread ID of the persistent PR and issue automation host before enabling the
webhook. Keep that host unarchived. The current deployment uses
`T-01a0c4b8-dd82-72c6-a2d8-23b9fa401492`. If the key is absent or invalid, the
plugin logs the problem and parents review threads under the webhook owner.
Changing the host creates one replacement thread for each PR or issue when its
next event arrives; existing threads cannot be reparented.

## Deploy the Cloudflare Worker

Authenticate Wrangler with the Cloudflare account that will own the Worker,
then test and deploy it:

```bash
cd apps/phlo-github-writer
npm ci
npm test
npm run deploy
```

The public App ID is configured in `wrangler.jsonc`. Add the two encrypted
Worker secrets through files or standard input so they do not appear in shell
history:

```console
npx wrangler secret put PHLO_GITHUB_APP_PRIVATE_KEY < /path/to/private-key.pem
npx wrangler secret put PHLO_GITHUB_PUBLISH_TOKEN < /path/to/publish-token
```

Set `PHLO_GITHUB_WRITER_URL` and `PHLO_GITHUB_WRITER_TOKEN` in the `iamgp/phlo`
Amp project. The URL is the deployed Worker origin. The token must match
`PHLO_GITHUB_PUBLISH_TOKEN`.

## Run scheduled maintenance

Create two private orb threads in the `phlo-maintenance` agent mode after the
plugin is active on `main`. Schedule these prompts:

The maintenance agent reads the saved prompt with `get_schedule`. On runtimes
that expose Amp tools as deferred modules, it uses `tool_search` and
`code_exec` to call `amp.get_schedule`. This does not grant permission to change
schedules. The agent runs resolvers and focused checks in its orb checkout;
GitHub writes still go through the writer tools. When publishing, omit a file's
`content` so the plugin reads the current workspace file instead of resending
large locks through the model.

Every maintenance wake-up starts with the advisory remediation pass in
`phlo-github:upstream-sync`, before following the rest of its saved prompt. It
reads outstanding scanner issues directly, including bot-authored issues that
the review webhook ignores. A matching issue is a work item, not a reason to
skip a fix. The agent resolves all affected consumers, verifies the candidate
locks and focused checks, then opens or updates a draft through the writer.
Existing remediation PRs take precedence. Blocked fixes stay in their existing
issues, with the blocker reported in the maintenance result. The one-artifact
limit applies to the entire wake-up, including both security and routine work.

### Daily dependency security

Run at `0 2 * * *` UTC:

> Run Phlo's focused dependency-security pass against current `origin/main`.
> Load `phlo-github:upstream-sync` and complete its advisory remediation pass.
> Reconcile the latest main-branch assessment and outstanding scanner issues,
> including bot-authored issues, with current main. Prefer a verified mechanical
> draft PR over another advisory issue. Reuse existing remediation PRs and retain
> existing issues for blocked fixes. Stop after this security-only pass. Create
> or update at most one artifact through the Phlo maintenance publishing tools.
> Never merge, waive a finding, publish, or release.

### Repository maintenance

Run at `0 8 * * 2,4` UTC:

> Run Phlo's scheduled maintenance pass against current `origin/main`. Load and
> follow both `phlo-github:upstream-sync` and `phlo-github:repo-health`. Search
> existing issues and pull requests before proposing work. Create or update at
> most one grounded issue or bounded draft pull request through the Phlo
> maintenance publishing tools. Create nothing when no action is warranted.
> Never merge, publish, release, change workflows or Amp automation, or modify
> secrets.

Keep the webhook owner thread and both maintenance threads unarchived. An
archived thread does not run its webhook or schedule.

## Update existing automation threads

Do not assume an existing custom-agent thread can switch modes or adopt a new
agent definition after a plugin reload. Each orb also has its own checkout.

After merging, create a replacement PR and issue host in
`phlo-automation-host` and two replacement threads in `phlo-maintenance` from
current `main`. Copy the exact saved prompts, recurrence rules, timezones, and
run modes from the old maintenance schedules. Verify that the replacements can
retrieve their saved prompts without running an audit, then pause the old
schedules to prevent duplicate runs.

For changes limited to maintenance behaviour, replace only the maintenance
threads. The host and webhook registration do not need to change. Use the
security prompt above when the old prompt stops on a clean scan without checking
outstanding issues. Preserve the existing recurrence, timezone, and run mode.
Get operator approval before changing live schedules. Reloading a plugin in a
development orb does not activate these changes in existing scheduled threads.
Deploy writer changes separately with operator approval before relying on a
larger multi-lock request budget. A repository merge alone does not update the
Cloudflare Worker.

Set `phloGitHubAutomationHost` to the replacement host's thread ID. Verify that
the host can reload plugins and send a status message. Check webhook ownership
and delivery separately: changing the parent configuration does not transfer
webhook ownership. Do not delete or recreate a webhook registration without
operator authorization. Keep the old threads available until cutover is
verified, and do not archive the active webhook owner.
