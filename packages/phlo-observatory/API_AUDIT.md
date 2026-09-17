# Observatory API Audit — what exists vs what 25 screens need

Source: phlo-api route inventory (origin/main-era contracts). UI scaffold is
UI-only; `web/src/data/demo.ts` mocks everything below marked MOCK.

## 1. Endpoint inventory (exists in phlo-api)

| Method + path | Models | Notes |
|---|---|---|
| GET /overview | counts, activity | home stats |
| GET /capabilities | flags[] | feature gates |
| GET /search | {kind, title, meta}[] + tabs | global search |
| GET /services | ServiceStatus[] | service health |
| GET /operations | Operation[] | ops list |
| GET /runs + POST /runs/{id}/retry|cancel | Run{id, status, spans} | pipeline runs |
| GET /actions | Action[] | available actions |
| GET /datasets, /datasets/facets, /datasets/{id}/profile | DatasetRow{well,target,ct,qty,flag} | dataset explorer |
| GET /assets, /assets/graph, POST /assets/materialize|backfill | AssetNode, Edge | lineage graph |
| GET /tables, /tables/{id}/preview, POST /tables/query, /tables/saved, /tables/diff | Table, Field{name,type}, SavedView | schemas + sheets |
| GET /row-journey | JourneyStep[] | record lineage |
| GET /quality | QCCheck[] | flags |
| GET /logs | LogEntry[] | run logs |
| GET /branches | Branch[] | nessie branches |
| GET /extensions, /extensions/settings | Manifest, NavItem, Route | plugin slots |
| GET /settings/preferences | Prefs{scope} | scoped settings |
| POST /workflow-wizard | WizardStep[] | guided flows |

## 2. Gap table — 25 screens → MISSING APIs

MOCK = currently served by `demo.ts`. MUST-ADD = phlo-api must add.

| # | Screen (route) | Uses existing | MOCK → MUST-ADD |
|---|---|---|---|
| 1 | Home `/` | /overview, /search | MOCK: alerts, continue-working 7 rows, today/priorities/activity → needs `GET /work` |
| 2 | Projects `/projects` | — | MOCK: projects[6] → `CRUD /projects` |
| 3 | Project detail `/projects/$projectId` | /row-journey | MOCK: 8 records + inspector → `GET /projects/{id}/records` |
| 4 | Notebook `/notebook/$experimentId` | /tables/{id}/preview | MOCK: materials, 10-row matrix, files → `CRUD /notebooks`, `/notebooks/{id}/matrix`, `/files` |
| 5 | Analysis `/analysis` | /datasets/{id}/profile | MOCK: spec+preview → `CRUD /analysis-specs`, `POST /analysis/runs` |
| 6 | Sheet `/sheets/$sheetId` | /tables/{id}/preview, /tables/saved | MOCK: 12-row grid → `GET /sheets/{id}/grid` (or reuse tables) |
| 7 | Plate `/plates/$designId` | — | MOCK: 96-well A1 config → `CRUD /plates/designs`, `GET /plates/{id}/wells` |
| 8 | Samples `/samples` | /tables (samples) | MOCK: samples[8]+inspector → `CRUD /samples`, `CRUD /lots`, `GET /samples/{id}/lineage` |
| 9 | Reagents `/reagents` | /tables (reagents) | MOCK: reagents[8] → `CRUD /reagents`, `GET /reagents/expiry` |
| 10 | Dataset `/datasets/$datasetId` | /datasets+facets+profile | MOCK: 8 rows + chart placeholder → exists; needs `GET /datasets/{id}/rows?filter` paging |
| 11 | Schemas `/schemas` | /tables+diff | mostly exists; MOCK ER links → `GET /tables/graph` |
| 12 | Storage `/storage` | — | MOCK: FZR-04 hierarchy, Box A5 → `CRUD /storage/locations`, `POST /storage/movements` |
| 13 | Templates `/templates` | — | MOCK: templates[12] → `CRUD /templates`, `POST /templates/{id}/instantiate` |
| 14 | Search `/search` | /search | exists; needs scoped facets per kind |
| 15 | Reviews `/reviews` | — | MOCK: reviews[8] KPIs → `CRUD /reviews`, `POST /reviews/{id}/sign`, e-sign chain |
| 16 | Calendar `/calendar` | — | MOCK: events → `CRUD /bookings`, conflict check |
| 17 | Planner `/planner` | /workflow-wizard | MOCK: tasks lanes → `CRUD /planner/tasks` |
| 18 | Instruments `/instruments` | /runs, /logs | MOCK: queue+trace → `CRUD /instruments`, `GET /instruments/{id}/calibration`, run trace exists |
| 19 | Lineage `/lineage` | /assets+graph | exists; needs record-level edges (sample→report) |
| 20 | Report `/reports/$reportId` | /actions | MOCK: reports[9], builder sections → `CRUD /reports`, `POST /reports/{id}/exports` |
| 21 | Pipelines `/pipelines` | /operations, /runs | exists for runs; MISSING lab-pipeline authoring `CRUD /pipelines` |
| 22 | Agents `/agents` | /actions | MOCK: agents[8] → `CRUD /agents`, `GET /agents/{id}/runs`, policy eval |
| 23 | Integrations `/integrations` | /services | MOCK: connector runs → `CRUD /integrations/connectors`, webhook deliveries |
| 24 | Admin `/admin` | /extensions | MOCK: users/roles/tokens/audit → `CRUD /admin/users`, `/roles`, `/tokens`, `GET /audit` |
| 25 | Settings `/settings` | /settings/preferences, /extensions/settings | exists for prefs; MOCK token gallery → static CSS vars, no API needed |

## 3. demo.ts → API swap plan

1. Keep `demo.ts` exports typed; each becomes a query hook return type.
2. Priority order: projects, notebooks/matrix, samples/lots, reviews/sign,
   storage/movements, templates/instantiate, bookings, planner, instruments/cal,
   reports/exports, pipelines-authoring, agents, integrations, admin/audit.
3. Datasets, tables, assets/graph, runs, search, preferences already have
   server contracts — wire first, delete corresponding demo exports.
