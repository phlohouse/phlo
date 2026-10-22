# Mission Control acceptance record

The screenshots and live observations below were captured on **22 October
2026** in the original local Mission Control testing deployment. They are
historical acceptance evidence for the current-source implementation, not
results produced by a fresh checkout of this curated example. Generated runtime
identities, policy grants and state are deliberately absent from this directory.

## Portable example checks

The generator creates deterministic source files for a fixed timestamp. Its
focused tests verify the actual row grains, 37/96 QC failures across ten batches,
four plate replicates, unique telemetry keys, 504 minute groups, mixed dates and
46 stability qualifiers. The workflow source declares 14 baseline assets, six
checks and eight jobs, with no schedule. Dagster's automatic asset job is separate.

The original live deployment used locally built service/provider images and a
source-built native engine. The curated example's complete container startup,
preview TLS setup and authorization bootstrap have not been rerun from scratch.
Those remain a reproduction gate; unit tests do not establish that runtime exit.

## Historical live acceptance

| Surface | Actual observation | Limit |
| --- | --- | --- |
| Baseline | 14 main Iceberg tables, 24 batches, six actual checks; four passed and two failed at the repaired observation | Materialization freshness ages; this is not the reference 148-table estate |
| Query | Saved chart and pivot restore actual settings; each runs 12 real rows. Read-only preview cap/ordering/write-denial checks passed | Saved views do not publish datasets or grant sharing |
| QC incident | 37 actual out-of-spec rows among 96, spanning ten batches, with current failing-row preview | Current data and detection history are separate |
| Stability review/load | CSV has 1,284 rows/11 original columns, 1,201 UK-style and 83 ISO dates; derived values preserve 23 ND/23 `<LOQ`, 46 numeric nulls and one normalized date | CSV, not XLSX; one-time isolated candidate, no watched folder/append/promotion |
| Native editor | Two immutable bioreactor revisions, source comparison and genuine 504-row candidate with native run/query/snapshot identity | Existing main uses `do_pct`; rounding changes do not repair a `do_sat_pct` rename or commit to Git |
| Loads | 18 authorized observations: three creator-private CSV candidates, one prior attempt and 14 ingestion-run observations including two failures | Different grains are not summed; missing file/time/byte/count evidence stays unknown |
| Governance | Five draft products, three draft metric models/eight measures; scoped preview returns held 10/results 37 and released 14/results zero | No publication, client sharing, verified-question claim or policy override |
| Incidents/inbox | Real persisted timeline events; actor-private read receipts, QC subscription and pinned ELISA decision save/readback | No fabricated messages, signature bypass, acknowledgement/resolution or signed merge |

Actual candidate identities retained for traceability:

- Stability upload `21fe25c50bf8416891900d2f04c99407`, review revision 2;
  Dagster run `e4fb3246-eb8e-4b4f-aeb2-565ef6dc8625`;
  snapshot `2644704975374588280`.
- Bioreactor draft `07171e9b-013b-4111-9384-c7afddfec767`, revision 2;
  native run `cdddc231-0697-4924-a2f0-806e25652118`;
  snapshot `1720297279182967631`.
- Earlier LIMS successor loaded 96 rows and batch-release native execution
  materialized 24 rows. Both exact completed run/snapshot identities still
  replayed unchanged after the qualifier/history changes. Two original failed
  CSV attempts and an empty partial destination were preserved separately;
  neither was relabelled successful or deleted to force a retry.

The native and CSV candidates preserved the original `main` head and workspace
SQL. Final post-restart idempotent replays and the 11 live read-only preview/server
rejection checks passed. No candidate promotion or merge occurred.

## Query preview follow-up checks

The original local deployment was checked again on 22 October 2026 while fixing
the duplicated readiness messages. These are observations of that deployment,
not a production readiness attestation or a fresh-example startup result.

| Check | Observed result |
| --- | --- |
| Authenticated catalog reads and evidence | Actual QC result counts were 96 rows and 37 out of spec; query evidence remained readable after the API restarted |
| TLS and authentication | Untrusted CA, wrong hostname, absent credentials and wrong credentials were rejected |
| Read-only isolation | API and coordinator rejected writes, cross-catalog system reads and session overrides |
| Bounds and cancellation | Five-row cap retained truncation evidence; an oversized response was rejected; a CPU-bound read hit Trino's `EXCEEDED_TIME_LIMIT` at its 20-second limit; cancellation succeeded |
| Coordinator restart | A separate source-provisioned local coordinator read the same 96 rows before and after restart |
| Password and certificate renewal | The separate coordinator accepted a newly generated complete bundle, new password and new CA; its previous password returned 401 and its previous CA rejected the renewed certificate |
| UI recovery | Query and Settings showed one neutral connected status, an actual probe timeout stayed actionable, and refreshing after load cleared restored connected status; a browser query returned 100 real rows |

The running API had one container and one Uvicorn process. Active query sessions
remain process-local; recovery of completed evidence does not imply recovery of
in-flight sessions. Renewal was rehearsed on a disposable coordinator, preserving
the working lakehouse's credentials and data. Physical scan limits retain the
periodic-enforcement limitations described in the preview deployment guide.

The automatic readiness probe verifies configuration and an authenticated
connection. Its connected status does not record or certify this manual
checklist, which must be repeated for another deployment or changed credentials.

## Screenshots

These seven JPEGs are current application captures from that deployment, not the
59 supplied design originals. They contain synthetic data and local development
identities. The desktop/phone checks include a 390 px phone body and a 350 px
Loads search; source filenames and long identifiers wrap inside the page.

| Screen | Desktop | Phone |
| --- | --- | --- |
| Stability date/qualifier review | [Desktop](evidence/parity-stability-review-desktop.jpg) | [Phone](evidence/parity-stability-review-mobile.jpg) |
| Native source comparison | [Desktop](evidence/parity-bioreactor-compare-desktop.jpg) | [Phone](evidence/parity-bioreactor-compare-mobile.jpg) |
| Native run history | — | [Phone](evidence/parity-bioreactor-history-mobile.jpg) |
| Loads inventory/evidence | [Desktop](evidence/parity-loads-desktop.jpg) | [Phone](evidence/parity-loads-mobile.jpg) |

The original comparison individually tracked all 59 design references and
retained 57 runtime captures. Its 25 MiB embedded report, design originals and
machine state are not part of this portable example. Full parity was not claimed.

## Remaining differences

XLSX workbook/sheet review, watched folders, append loads, provider-wide load
package history/schema policy/replay, native downstream/test/Git-commit flows,
Ask, governed publication/client sharing, cost/catalog onboarding and genuine
all-clear/staging/resolved/signed scenarios remain incomplete. Candidate data
branches are distinct from Git source changes and published datasets.

No original `.phlo` content, runtime volumes, CA/passwords, tokens, source binary,
authorization grants, destructive cleanup/identity-repair scripts or UUID-bound
candidate contracts were copied. Reproduction creates fresh identities through
normal authorized APIs and retains unknown outcomes for reconciliation.
