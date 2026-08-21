# Release Candidate Protection

`release candidate / status` is the single required, fail-closed status for
release candidates on `main` and `beta`. It is emitted only after CI (including
every installed-provider-artifact shard and recovery drill), integration,
security, and the release golden path succeed for the same candidate SHA.

The aggregate uploads `release-candidate-evidence-<sha>` with the SHA and each
constituent conclusion. Retain the GitHub Actions run and that artifact for
release and recovery audit records.

## Applying the ruleset

The versioned ruleset specification is
[`security/release-candidate-ruleset.json`](../../security/release-candidate-ruleset.json).
An organization owner must replace
`REPLACE_WITH_RELEASE_EMERGENCY_TEAM_ID` with the numeric ID of the dedicated,
audited `release-emergency` team before applying it. Do not substitute a broad
repository role or an individual account.

```bash
team_id="$(gh api orgs/phlohouse/teams/release-emergency --jq .id)"
jq --argjson team_id "$team_id" \
  '(.bypass_actors[] | select(.actor_type == "Team") | .actor_id) = $team_id' \
  security/release-candidate-ruleset.json > /tmp/release-candidate-ruleset.json
gh api --method POST repos/phlohouse/phlo/rulesets \
  --input /tmp/release-candidate-ruleset.json
```

The pull-request rule blocks direct pushes and requires a fresh approving review.
The required-status rule blocks failed, cancelled, skipped, or missing candidate
evidence. Only the emergency team may bypass either rule; GitHub records every
bypass in the ruleset audit log. Review that log and attach the incident record
before using the emergency path.

After applying or changing the ruleset, save a fresh ruleset snapshot with the
release evidence:

```bash
gh api repos/phlohouse/phlo/rulesets > rulesets-$(date +%F).json
```

## Release and recovery

`release-tag` polls GitHub's checks API for a successful
`release candidate / status` on the exact pushed release SHA before invoking
ReleaseX. The publish workflow repeats the check for the checked-out tag target.
Neither accepts branch-level, stale, or skipped evidence, including during tag
or publish recovery.

For a fixture dry run, open one in-repository PR against each protected branch,
verify the aggregate artifact names the candidate build SHA, and retain links to
each constituent workflow plus the aggregate run. The existing transactional
ReleaseX workspace configuration remains the source of truth for preparing the
release commit; the ruleset does not weaken that transaction.
