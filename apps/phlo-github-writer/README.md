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
  `main` SHA and opens a draft pull request. It refuses changes under `.github/`,
  `.agents/`, `.amp/`, and `.git/`.

All write routes require `Authorization: Bearer <PHLO_GITHUB_PUBLISH_TOKEN>`.
The GitHub App key never leaves the server.

The webhook plugin keeps one private review thread per pull request or issue and
reuses it for later accepted events. In a pull request review thread, ask
phlo-agent directly to update that pull request's title or description. On
GitHub, an owner, member, or collaborator can invoke the same thread by starting
a new issue, pull request, or review comment with `@phlo-agent`.

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

### Daily dependency security

Run at `0 2 * * *` UTC:

> Run Phlo's focused dependency-security pass against current `origin/main`.
> Load `phlo-github:upstream-sync`. Inspect the latest public main-branch
> security CI result and authoritative upstream advisories. If they are clean,
> stop and create nothing. For findings, search existing issues and pull
> requests, then follow the skill. Create at most one issue or bounded draft
> pull request through the Phlo maintenance publishing tools. Never merge,
> waive a finding, publish, or release.

### Repository maintenance

Run at `0 8 * * 2,4` UTC:

> Run Phlo's scheduled maintenance pass against current `origin/main`. Load and
> follow both `phlo-github:upstream-sync` and `phlo-github:repo-health`. Search
> existing issues and pull requests before proposing work. Create at most one
> grounded issue or bounded draft pull request through the Phlo maintenance
> publishing tools. Create nothing when no action is warranted. Never merge,
> publish, release, change workflows or Amp automation, or modify secrets.

Keep the webhook owner thread and both maintenance threads unarchived. An
archived thread does not run its webhook or schedule.
