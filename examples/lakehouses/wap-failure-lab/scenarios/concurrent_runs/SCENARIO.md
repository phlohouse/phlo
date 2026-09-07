# Scenario: concurrent_runs

Submit two partitions before waiting for either report. Each launch has its
own WAP branch and exact logical run identity. Both launch reports must record
the same starting main hash; otherwise the scenario fails rather than counting
serial publications as overlap.

- Partition A: 12 rows, batch IDs b-6001 through b-6012.
- Partition B: 8 rows, batch IDs b-7001 through b-7008.

Run `python scripts/run_scenario.py concurrent_runs` against the running lab.
Both files stay staged until both reports reach a terminal state. Distinct
batch IDs do not guarantee merge compatibility for concurrent table metadata
updates: at least one run must publish, and a catalog conflict may safely
reject the other. The scheduler controls actual execution interleaving.

Each successful batch adds exactly its expected rows and removes its branch.
Each failed batch adds zero rows and retains its branch for recovery. The
total delta must equal only the successful writes; both branches must be
distinct. Missing or unfinished reports fail the scenario.

The source uses append mode. Repeat successful deliveries append again; they
are not upserts or a proof of idempotency after a committed write.
