# Dependency assessment and remediation

The security workflow reads every tracked `uv.lock`, `package-lock.json` and
`npm-shrinkwrap.json` from `git ls-tree` at the exact assessment SHA, including
examples. It never installs dependencies or executes inspected revision code.
The historical writer lock path maps to its current product identity. Other
lock additions and moves introduce risk; deletion removes that inventory.
Workspace and Git dependencies have no registry version to query and are outside
the OSV registry assessment. Unsupported registry sources fail closed.

## Verdicts

`scripts/dependency_delta.py --base <SHA> --head <SHA> --output <file>` queries
each distinct registry version once, sharing advisory results between revisions.
Introduced vulnerable versions block at every severity. `--mode queue` performs
a fresh full assessment and additionally blocks all existing findings without a
valid reviewed risk record. `--mode release` blocks every finding, ignoring risk
acceptance. Exit codes are 0 (accepted), 1 (risk blocks), 2 (assessment unavailable).
Structured artifacts include inventory and introduced/existing findings even
when risk blocks; scanner errors cannot produce a clean verdict.

`security/dependency-risks.json` is deliberately empty. Baseline cleanup belongs
to #1063, not automatic grandfathering. Each future record must specify the exact
lockfile, ecosystem, name, version, advisory, severity, Boolean exploited status,
owner, rationale, first_seen, expires, reviewed_by and review_url. Dates must be
timezone-aware ISO 8601. The maximum window starts at first_seen: critical or
exploited 24 hours, high 7 days, moderate/low 30 days. Unknown severity, malformed,
duplicate or expired records fail closed. OSV severity must confirm the record;
missing severity cannot be waived. Remove expired records rather than resetting
first_seen. Human maintainers must verify exploitation status and first-seen
evidence during review; these cannot be reliably inferred from registry queries.

Risk changes require independent human review through protected pull requests.
An agent must never approve its own exception. The script verifies the merged
main PR, exact ledger record and final revision, authenticated human approval
and repository maintainer permission. A later comment does not revoke approval;
changes requested or dismissal do. Use the ledger from the tested trusted base,
never the proposed revision's ledger. GitHub review protection remains necessary.

## Daily ownership and handoff

The daily security schedule produces a complete assessment independent of agent
availability. Repository maintainers own unavailable scans and unaccepted risks;
critical/exploited findings require immediate escalation to the security contact
in SECURITY.md, high findings same-day assignment, and other findings assignment
before the next daily scan. Deduplicate remediation by advisory, package and
version, retaining all affected lockfiles and named owners in the existing issue.

Phlo's existing `phlo-maintenance` mode coordinates triage; Renovate remains the
deterministic resolver and protected CI verifies its updates. The existing agent
interface requires a configured schedule, searches issues/PRs before publishing,
has no shell, and permits one bounded issue or draft PR via
`publish_phlo_maintenance_issue` / `publish_phlo_maintenance_pull_request`.
It cannot run scanners, approve risks, change workflows or change live settings.
The trusted-main `dependency-triage.yml` workflow authenticates the completed
scheduled Security run and its exact SHA/attempt, verifies its JSON artifact,
and reuses matching advisory/package PRs before creating or updating deduplicated
security issues. PR comments preserve all consumer paths; ordinary version bumps
do not count as advisory remediation. Unavailable scans create
maintainer-owned escalation issues. Configure `PHLO_DEPENDENCY_TRIAGE_TOKEN`
with Issues write, Pull requests write, Actions read and Contents read and a human
sender to reach existing signed issue webhook intake; its bot filter
rejects the fallback `github.token` sender. Without this credential, issues are
recorded but maintainers own triage. Verify live webhook delivery before claiming
automatic handoff. No competing updater, agent shell permission or shared setting
is introduced; Renovate remains the resolver.

## Caller contract

PR callers use delta mode with exact base/head SHAs; merge-queue callers use queue
mode with the queue base/head, without changed-path shortcuts. Release callers
use release mode. The reusable workflow executes the script and ledger from
trusted default-branch checkout; callers must also use a trusted workflow
definition. Ensure inspected commits are available in the object database.
The workflow no longer builds documentation: retain that gate in the docs lane.
