# Schema evolution and contracts

A schema is a promise about the shape and meaning of data that a consumer can read. This post explains additive and breaking changes, how Phlo snapshots and checks contracts, and how a branch-first rollout gives you a place to test a change before it reaches consumers.

## The problem from first principles

A producer can change a table without intending to break anyone. Renaming a column, changing a type, removing a value, or making a field nullable can look local in the producer's code. A consumer sees a different contract and may fail much later.

An additive change usually gives consumers more information without invalidating their existing query. A new nullable column is often additive. A breaking change removes an existing field, changes its meaning, or changes its type in a way the consumer cannot read. The classification depends on what consumers rely on, not only on the syntax of the migration.

Contracts make that expectation visible. A snapshot records the schema that was accepted at one point. A check compares the current table with the previous snapshot. A migration plan describes the deliberate change when compatibility is not automatic.

Compatibility has more than one audience. A query may continue to parse after a column changes type while its calculations become wrong. An additive column may be safe for one consumer and invalid for another consumer that checks an exact projection. Classify the change with known consumers, then use the technical snapshot as evidence rather than as the whole decision.

## How Phlo approaches it

Phlo stores schema snapshots in the configured registry database. `phlo contracts snapshot` stores a snapshot for a table, and `phlo contracts check` compares the table with its previous snapshot. Normal materialisation refreshes contracts unless you pass `--no-contract-refresh`.

`phlo schema-migrate` handles planned changes between quality schemas and storage tables. Its command group includes `diff`, `plan`, `apply`, `history`, contract export, and migration scaffolding. The exact operation depends on whether you are inspecting a difference, preparing a plan, or applying a change.

The migration workflow keeps inspection separate from mutation. `diff` shows pending changes. `plan` creates the intended migration. `apply` changes the storage table. `history` shows earlier schema versions. The scaffold commands create YAML instructions that you can review before applying them.

The safest rollout is staged. First describe the intended consumer contract. Then compare the current table with its snapshot. Next prepare a branch and run the quality and governance checks there. Finally review the migration plan and merge or apply the change with the people who own the affected consumers.

Keep the old contract available during a compatibility window when consumers cannot move at once. An additive field can be introduced before a consumer starts reading it. A renamed field may need a new name and a deprecation period rather than an in-place rename. The contract process gives that choice a record.

Make the compatibility decision visible to the people who own the consumer.

Nessie branches provide an isolated rollout surface. You can create a branch, materialise or inspect a candidate state, compare it with `main`, and merge only after the contract, quality, and governance checks are ready. `phlo governance check` reports whether the project's declarations meet its readiness rules.

The normal asset execution path remains Dagster. A contract check is part of the evidence around a Dagster run. A migration command is a deliberate state change and should be planned before it is applied.

## Try it

Create or refresh the contract snapshot for a table:

```bash
phlo contracts snapshot --table raw.events --schema-file <schema-file>
phlo contracts check --table raw.events --fail-on breaking
```

The snapshot command requires `--table` and `--schema-file`. The check command requires `--table` and can use `--fail-on breaking` or `--fail-on warning` when a compatibility result should fail the command.

Inspect the schema migration operations:

```bash
phlo schema-migrate diff raw.events
phlo schema-migrate plan raw.events
```

Prepare a branch for a change:

```bash
phlo branch create feature/schema-change
phlo branch diff feature/schema-change main
```

Run the governance readiness check:

```bash
phlo governance check
```

Do not apply a migration until you have reviewed its plan and confirmed its target. The migration command changes storage state and should be run only after the compatibility result requires that change.

## Mental model to keep

- A schema describes both shape and consumer expectations.
- Additive does not always mean safe if a consumer rejects unknown fields.
- A snapshot is evidence of an accepted schema state.
- A branch isolates a candidate data state from the consumer reference.
- A migration is a deliberate change, not a repair for an unexplained failure.
- Governance readiness adds ownership and consumer context to the technical contract.

## Where this goes wrong

- **A type change is treated as additive.** Contract comparison exposes the difference before consumers discover it in production.
- **A snapshot is refreshed without reviewing the change.** The new state can hide the evidence that a breaking change occurred.
- **A branch is merged before checks finish.** Use branch diff and governance readiness as gates before promotion.
- **A migration is applied to the wrong target.** Review the plan, target, and confirmation requirements immediately before applying it.
- **A source change passes schema checks but changes meaning.** Add a consumer-facing contract review because structural compatibility does not prove semantic compatibility.

## Next

Continue with [Observability](09-observability.md) to learn how to see contract failures, run evidence, and alerts. For procedures, read [Evolve a schema](../guides/evolve-a-schema.md), [Governance and datasets](../concepts/governance-and-datasets.md), and [CLI](../reference/cli.md).
