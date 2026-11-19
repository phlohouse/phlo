/** Controlled HTTP fixture for the built-app smoke, never live-service evidence. */
import { createHash } from 'node:crypto'
import { createServer } from 'node:http'

const sqlCell = (id, name, source, engine = 'trino') => ({
  id,
  kind: 'sql',
  source,
  name,
  engine,
})
const NOTEBOOK_FILES = {
  'market/autoimmune-car-t.ipynb': [
    {
      id: 'intro',
      kind: 'markdown',
      source:
        '# Autoimmune CAR-T: how fast is it growing?\n\nCAR-T started in blood cancers. A growing share of new CAR-T trials now target **autoimmune** disease. This notebook sizes that shift from registry data already in the lakehouse.',
      name: null,
      engine: null,
    },
    sqlCell(
      'trials',
      'trials',
      "-- Interventional CGT trial starts by modality\nSELECT modality,\n       year(start_date) AS start_year,\n       count(*) AS starts\nFROM gold.cgt_trial_starts\nWHERE start_date >= DATE '2019-01-01'\nGROUP BY 1, 2\nORDER BY 2, 1",
    ),
    {
      id: 'growth-md',
      kind: 'markdown',
      source:
        '## Growth by modality\n\nThe `trials` result from the cell above is already in the kernel, so Python does not query again.',
      name: null,
      engine: null,
    },
    {
      id: 'growth',
      kind: 'python',
      source:
        'growth = trials[trials.start_year == 2025].sort_values("starts", ascending=False)\nprint("modalities", len(growth))',
      name: 'growth',
      engine: null,
    },
    sqlCell(
      'share',
      'autoimmune_share',
      "SELECT start_year,\n       sum(starts) FILTER (WHERE modality = 'CAR-T, autoimmune') / sum(starts) AS share\nFROM trials\nGROUP BY 1 ORDER BY 1",
      'duckdb',
    ),
  ],
  'market/trial-starts-by-region.ipynb': [],
  'qc/batch-release-times.ipynb': [],
}
const TRIAL_ROWS = [
  ['CAR-T, oncology', 2025, 176],
  ['AAV gene therapy', 2025, 37],
  ['NK and CAR-NK', 2025, 30],
  ['TCR-T', 2025, 29],
  ['TIL', 2025, 27],
  ['CAR-T, autoimmune', 2025, 96],
  ['CAR-T, oncology', 2024, 164],
  ['CAR-T, autoimmune', 2024, 61],
].map(([modality, start_year, starts]) => ({ modality, start_year, starts }))
const fixtureTables = {
  trials: {
    columns: [
      { name: 'modality', type: 'varchar' },
      { name: 'start_year', type: 'bigint' },
      { name: 'starts', type: 'bigint' },
    ],
    rows: TRIAL_ROWS,
    row_count: 42,
  },
  growth: {
    columns: [
      { name: 'modality', type: 'object' },
      { name: 'start_year', type: 'int64' },
      { name: 'starts', type: 'int64' },
    ],
    rows: TRIAL_ROWS.slice(0, 6).sort((a, b) => b.starts - a.starts),
    row_count: 6,
  },
  autoimmune_share: {
    columns: [
      { name: 'start_year', type: 'int32' },
      { name: 'share', type: 'double' },
    ],
    rows: [2019, 2020, 2021, 2022, 2023, 2024, 2025].map((year, index) => ({
      start_year: year,
      share: [0.011, 0.019, 0.03, 0.067, 0.167, 0.28, 0.353][index],
    })),
    row_count: 7,
  },
}

/** Notebook files and a scripted kernel, enough to drive the notebook page. */
const CATALOG = {
  bronze: { orders: 'table', trial_registry: 'table' },
  market: {
    trials: 'table',
    trial_starts: 'table',
    autoimmune_share: 'view',
  },
  qc: { batch_release: 'table', release_times: 'view' },
}
const COLUMNS = [
  ['nct_id', 'varchar', false],
  ['modality', 'varchar', true],
  ['indication', 'varchar', true],
  ['phase', 'varchar', true],
  ['start_date', 'date', true],
  ['enrollment', 'integer', true],
]

/** One page of the lakehouse browser: schemas, then a schema's tables, then a table's columns. */
function catalogObjects(url, env, ref) {
  const schema = url.searchParams.get('schema')
  const table = url.searchParams.get('table')
  const item = (name, kind, dataType = null, nullable = null) => ({
    name,
    kind,
    data_type: dataType,
    nullable,
  })
  let items
  if (!schema) items = Object.keys(CATALOG).map((name) => item(name, 'schema'))
  else if (!(schema in CATALOG)) items = []
  else if (!table)
    items = Object.entries(CATALOG[schema]).map(([name, kind]) =>
      item(name, kind),
    )
  else
    items = COLUMNS.map(([name, type, nullable]) =>
      item(name, 'column', type, nullable),
    )
  return {
    env,
    nessie_ref: ref,
    catalog: 'lake',
    schema_name: schema,
    table,
    items,
    next_offset: null,
  }
}

const sha256 = (text) => createHash('sha256').update(text).digest('hex')
const ACCESS = { edit: true, run: true, schedule: true, publish: true }
const digestOf = (file) =>
  sha256(JSON.stringify([file.cells, file.packages, file.schedule]))

function notebookView(env, ref, path, file, access) {
  return {
    env,
    path,
    digest: digestOf(file),
    nessie_ref: ref,
    cells: file.cells,
    packages: file.packages,
    schedule: file.schedule,
    access,
  }
}

function catalogDetails(url, env, ref) {
  const schema = url.searchParams.get('schema')
  const table = url.searchParams.get('table')
  const kind = CATALOG[schema]?.[table]
  if (!kind) return { status: 404, body: { detail: 'Not found' } }
  return {
    status: 200,
    body: {
      env,
      nessie_ref: ref,
      catalog: 'lake',
      schema_name: schema,
      table,
      kind,
      comment:
        kind === 'view'
          ? 'Share of new CAR-T trials that target autoimmune disease.'
          : 'Interventional trial registrations, one row per trial.',
      columns: COLUMNS.map(([name, type], index) => ({
        name,
        data_type: type,
        comment:
          [
            'ClinicalTrials.gov identifier',
            'Therapy class',
            null,
            null,
            'First patient in',
            null,
          ][index] ?? null,
      })),
      ddl:
        kind === 'view'
          ? `CREATE VIEW lake.${schema}.${table} AS\nSELECT start_year, share\nFROM lake.market.trial_starts`
          : `CREATE TABLE lake.${schema}.${table} (\n${COLUMNS.map(([name, type]) => `   ${name} ${type}`).join(',\n')}\n)\nWITH (\n   format = 'PARQUET',\n   partitioning = ARRAY['year(start_date)']\n)`,
      row_count: kind === 'view' ? null : 18_432,
      snapshot_id: kind === 'view' ? null : '5172305471230984411',
      committed_at: kind === 'view' ? null : '2026-11-19T06:02:11Z',
    },
  }
}

function catalogFixture(url, env, ref) {
  if (url.pathname === '/api/v1/query/catalog/details')
    return catalogDetails(url, env, ref)
  if (url.pathname === '/api/v1/query/catalog/sample')
    return {
      status: 200,
      body: {
        env,
        schema_name: url.searchParams.get('schema'),
        table: url.searchParams.get('table'),
        columns: COLUMNS.map(([name, type]) => ({ name, type })),
        rows: Array.from({ length: 12 }, (_, index) => ({
          nct_id: `NCT0${5512300 + index * 7}`,
          modality: ['CAR-T, autoimmune', 'CAR-T, oncology', 'TCR-T'][
            index % 3
          ],
          indication: ['Lupus nephritis', 'DLBCL', 'Synovial sarcoma'][
            index % 3
          ],
          phase: ['Phase 1', 'Phase 1/2', 'Phase 2'][index % 3],
          start_date: `2025-0${(index % 9) + 1}-1${index % 9}`,
          enrollment: 12 + index * 9,
        })),
        has_more: true,
      },
    }
  const q = (url.searchParams.get('q') ?? '').toLowerCase()
  const items = []
  for (const [schema, tables] of Object.entries(CATALOG))
    for (const [table, kind] of Object.entries(tables)) {
      if (table.includes(q) || schema.includes(q))
        items.push({
          schema_name: schema,
          table,
          kind,
          column: null,
          data_type: null,
        })
      for (const [column, type] of COLUMNS)
        if (column.includes(q) && kind === 'table' && schema === 'market')
          items.push({
            schema_name: schema,
            table,
            kind: 'column',
            column,
            data_type: type,
          })
    }
  return {
    status: 200,
    body: {
      env,
      catalog: 'lake',
      q,
      items: items.slice(0, 50),
      truncated: false,
    },
  }
}

function kernelRun(input, state) {
  state.kernels += 1
  const table =
    fixtureTables[input.name] ??
    (input.kind === 'trino' ? fixtureTables.trials : null)
  return {
    status: 'ok',
    execution_count: state.kernels,
    duration_ms: input.kind === 'python' ? 38 : 412,
    outputs:
      input.kind === 'python'
        ? [
            {
              type: 'stream',
              name: 'stdout',
              text: 'modalities 6\n',
              png: null,
              ename: null,
            },
          ]
        : [],
    truncated: false,
    table: input.kind === 'python' && !input.name ? null : table,
  }
}

function kernelFixture(method, url, body, env, state) {
  const parts = url.pathname.split('/')
  const id = decodeURIComponent(parts[5] ?? '')
  const kernel = state.running[id]
  if (!kernel) return { status: 404, body: { detail: 'Kernel not found' } }
  const action = parts[6]
  if (action === 'variables')
    return {
      status: 200,
      body: {
        items: Object.entries(fixtureTables).map(([name, table]) => ({
          name,
          type: 'pandas.DataFrame',
          shape: `${table.row_count} × ${table.columns.length}`,
          preview: table.columns.map((column) => column.name).join(', '),
        })),
      },
    }
  if (action === 'packages')
    return {
      status: 200,
      body: {
        requested: kernel.packages,
        items: [
          ...kernel.packages.map((name) => ({
            name: name.split(/[<>=!~ ]/)[0],
            version: '1.5.2',
            source: 'notebook',
          })),
          ...[
            ['duckdb', '1.1.3'],
            ['ipykernel', '6.29.5'],
            ['numpy', '2.1.3'],
            ['pandas', '2.2.3'],
            ['pyarrow', '18.1.0'],
          ].map(([name, version]) => ({ name, version, source: 'base' })),
        ],
      },
    }
  if (action === 'complete') {
    const input = JSON.parse(body)
    const word = /[\w.]*$/.exec(input.code.slice(0, input.cursor))?.[0] ?? ''
    const names = [
      'trials',
      'trials.groupby',
      'trials.head',
      'trials.sort_values',
      'growth',
    ]
    return {
      status: 200,
      body: {
        matches: names.filter((name) => name.startsWith(word) && name !== word),
        cursor_start: input.cursor - word.length,
        cursor_end: input.cursor,
      },
    }
  }
  if (action === 'runs' && method === 'POST') {
    const input = JSON.parse(body)
    const run = {
      id: `run-${Object.keys(state.runs).length + 1}`,
      polls: 0,
      input,
    }
    state.runs[run.id] = run
    return {
      status: 202,
      body: { id: run.id, kernel: id, status: 'running', result: null },
    }
  }
  if (action === 'runs') {
    const run = state.runs[decodeURIComponent(parts[7] ?? '')]
    if (!run) return { status: 404, body: { detail: 'Run not found' } }
    run.polls += 1
    if (run.polls < 4 && run.input.kind === 'python')
      return {
        status: 200,
        body: {
          id: run.id,
          kernel: id,
          status: 'running',
          result: {
            status: 'running',
            execution_count: null,
            duration_ms: 120,
            outputs: [
              {
                type: 'stream',
                name: 'stdout',
                text: 'loading…\n',
                png: null,
                ename: null,
              },
            ],
            truncated: false,
            table: null,
          },
        },
      }
    run.result ??= kernelRun(run.input, state)
    kernel.execution_count = run.result.execution_count
    return {
      status: 200,
      body: { id: run.id, kernel: id, status: 'ok', result: run.result },
    }
  }
  if (method === 'DELETE') {
    delete state.running[id]
    return { status: 204 }
  }
  return { status: 200, body: kernel }
}

function publishFixture(method, url, body, env, ref, state) {
  const path = url.searchParams.get('path')
  const file = state.files[path]
  if (url.pathname === '/api/v1/notebooks/outputs')
    return {
      status: 200,
      body: { items: state.outputs[path] ?? [], retention_days: 14 },
    }
  if (url.pathname === '/api/v1/notebooks/runs' && method === 'POST') {
    const run = {
      id: `nbrun-${Date.now()}`,
      trigger: 'manual',
      actor: 'browser-smoke',
      env,
      status: 'ok',
      started_at: new Date(Date.now() - 4200).toISOString(),
      finished_at: new Date().toISOString(),
      message: null,
      cells: (file?.cells ?? [])
        .filter((cell) => cell.kind !== 'markdown')
        .map((cell) => ({
          cell_id: cell.id,
          status: 'ok',
          duration_ms: 400,
          error: null,
        })),
    }
    state.history[path] = [run, ...(state.history[path] ?? [])]
    return {
      status: 202,
      body: { ...run, status: 'running', finished_at: null, cells: [] },
    }
  }
  if (url.pathname === '/api/v1/notebooks/runs')
    return {
      status: 200,
      body: { items: state.history[path] ?? [], schedules_enabled: true },
    }
  if (url.pathname === '/api/v1/notebooks/schedule') {
    const input = JSON.parse(body)
    if (input.expected_digest !== digestOf(file))
      return {
        status: 409,
        body: {
          error: {
            code: 'notebook_changed',
            message:
              'This notebook changed since you opened it. Reload it before saving.',
          },
        },
      }
    file.schedule = input.cron
      ? {
          cron: input.cron,
          env: input.env ?? env,
          owner: 'browser-smoke',
          enabled: input.enabled,
        }
      : null
    return {
      status: 200,
      body: notebookView(env, ref, path, file, state.access),
    }
  }
  if (url.pathname === '/api/v1/notebooks/git')
    return {
      status: 200,
      body: {
        enabled: true,
        branch: 'analysis/autoimmune',
        protected: false,
        changed: [
          'notebooks/market/autoimmune-car-t.ipynb',
          'workflows/transforms/dbt/models/notebooks/trial_starts_by_modality.sql',
          'workflows/transforms/dbt/models/notebooks/trial_starts_by_modality.yml',
        ],
        message: null,
      },
    }
  if (url.pathname === '/api/v1/notebooks/git/commit')
    return {
      status: 200,
      body: { branch: 'analysis/autoimmune', commit: 'a1b2c3d4e5f6a7b8c9d0' },
    }
  if (url.pathname === '/api/v1/notebooks/promote') {
    const input = JSON.parse(body)
    const cell = file?.cells.find((item) => item.id === input.cell_id)
    const sql = (cell?.source ?? '').replace(
      'gold.cgt_trial_starts',
      "{{ source('gold', 'cgt_trial_starts') }}",
    )
    return {
      status: 200,
      body: {
        applied: input.apply,
        files: [
          {
            path: `workflows/transforms/dbt/models/notebooks/${input.model}.sql`,
            content: `-- Promoted from notebooks/${path}, cell ${cell?.name}.\n{{ config(materialized='table') }}\n\n${sql}\n`,
            exists: false,
          },
          {
            path: `workflows/transforms/dbt/models/notebooks/${input.model}.yml`,
            content: `version: 2\nmodels:\n- name: ${input.model}\n  description: Promoted from notebooks/${path}, cell ${cell?.name} in Observatory.\n`,
            exists: false,
          },
        ],
      },
    }
  }
  return null
}

function contentFixture(method, url, body, env, ref, state) {
  const path = url.searchParams.get('path')
  const file = state.files[path]
  if (url.pathname === '/api/v1/notebooks/move') {
    const input = JSON.parse(body)
    if (!file) return { status: 404, body: { detail: 'Not found' } }
    if (state.files[input.to])
      return {
        status: 409,
        body: {
          error: {
            code: 'notebook_changed',
            message:
              'A notebook already exists there, or this one changed since you opened it.',
          },
        },
      }
    state.files[input.to] = file
    delete state.files[path]
    return {
      status: 200,
      body: notebookView(env, ref, input.to, file, state.access),
    }
  }
  if (method === 'DELETE') {
    if (!file) return { status: 404, body: { detail: 'Not found' } }
    delete state.files[path]
    return { status: 204 }
  }
  if (method === 'PUT') {
    const input = JSON.parse(body)
    const current = file ? digestOf(file) : null
    if (current !== input.expected_digest)
      return {
        status: 409,
        body: {
          error: {
            code: 'notebook_changed',
            message:
              'This notebook changed since you opened it. Reload it before saving.',
          },
        },
      }
    state.files[path] = {
      cells: input.cells,
      packages: input.packages ?? [],
      schedule: file?.schedule ?? null,
    }
  } else if (!file) return { status: 404, body: { detail: 'Not found' } }
  return {
    status: 200,
    body: notebookView(env, ref, path, state.files[path], state.access),
  }
}

function notebookFixture(method, url, body, env, ref, state) {
  if (url.pathname === '/api/v1/notebooks')
    return {
      status: 200,
      body: {
        env,
        root: 'notebooks',
        items: Object.keys(state.files).map((item) => ({
          path: item,
          size: 2048,
          modified_at: '2026-11-19T09:00:00Z',
        })),
        truncated: false,
      },
    }
  if (
    url.pathname === '/api/v1/notebooks/content' ||
    url.pathname === '/api/v1/notebooks/move'
  )
    return contentFixture(method, url, body, env, ref, state)
  const published = publishFixture(method, url, body, env, ref, state)
  if (published) return published
  if (url.pathname === '/api/v1/notebooks/kernels') {
    // The real API refuses the kernel list to someone who may not run code.
    if (!state.access.run)
      return {
        status: 403,
        body: { detail: { code: 'forbidden', message: 'Not allowed.' } },
      }
    if (method === 'POST') {
      const input = JSON.parse(body)
      const started = {
        id: `kernel-${Object.keys(state.running).length + 1}`,
        env,
        path: input.path,
        status: 'idle',
        message: null,
        packages: state.files[input.path]?.packages ?? [],
        execution_count: 0,
      }
      state.running[started.id] = started
      return { status: 201, body: started }
    }
    return { status: 200, body: Object.values(state.running) }
  }
  return kernelFixture(method, url, body, env, state)
}

function savedOutputs(files) {
  const cells = files['market/autoimmune-car-t.ipynb'].cells
  const trials = cells.find((cell) => cell.id === 'trials')
  return {
    'market/autoimmune-car-t.ipynb': [
      {
        cell_id: 'trials',
        source_sha256: sha256(trials.source),
        actor: 'gareth',
        finished_at: '2026-11-19T06:02:44Z',
        run: {
          status: 'ok',
          execution_count: 1,
          duration_ms: 1840,
          outputs: [],
          truncated: false,
          table: fixtureTables.trials,
        },
      },
      {
        cell_id: 'growth',
        source_sha256: sha256('growth = trials.head()'),
        actor: 'gareth',
        finished_at: '2026-11-19T06:02:46Z',
        run: {
          status: 'ok',
          execution_count: 2,
          duration_ms: 38,
          outputs: [
            {
              type: 'stream',
              name: 'stdout',
              text: 'modalities 6\n',
              png: null,
              ename: null,
            },
          ],
          truncated: false,
          table: fixtureTables.growth,
        },
      },
    ],
  }
}

export function startApiFixture({
  environments = [
    { env: 'prod', status: 'available' },
    { env: 'staging', status: 'available' },
  ],
  defaultEnvironment = environments[0].env,
  deniedEnvironments = [],
} = {}) {
  const requests = []
  const files = Object.fromEntries(
    Object.entries(structuredClone(NOTEBOOK_FILES)).map(([path, cells]) => [
      path,
      {
        cells,
        packages: path.startsWith('market/autoimmune')
          ? ['statsmodels>=0.14']
          : [],
        schedule: path.startsWith('market/autoimmune')
          ? { cron: '0 6 * * 1', env: 'prod', owner: 'gareth', enabled: true }
          : null,
      },
    ]),
  )
  const notebooks = {
    files,
    access: { ...ACCESS },
    outputs: savedOutputs(files),
    history: {
      'market/autoimmune-car-t.ipynb': [
        {
          id: 'nbrun-sched-2',
          trigger: 'schedule',
          actor: 'gareth',
          env: 'prod',
          status: 'ok',
          started_at: '2026-11-17T06:00:03Z',
          finished_at: '2026-11-17T06:00:51Z',
          message: null,
          cells: ['trials', 'growth', 'share'].map((cell_id) => ({
            cell_id,
            status: 'ok',
            duration_ms: 900,
            error: null,
          })),
        },
        {
          id: 'nbrun-sched-1',
          trigger: 'schedule',
          actor: 'gareth',
          env: 'prod',
          status: 'failed',
          started_at: '2026-11-10T06:00:02Z',
          finished_at: '2026-11-10T06:00:14Z',
          message: null,
          cells: [
            { cell_id: 'trials', status: 'ok', duration_ms: 1200, error: null },
            {
              cell_id: 'growth',
              status: 'error',
              duration_ms: 20,
              error: 'KeyError',
            },
          ],
        },
      ],
    },
    kernels: 0,
    runs: {},
    running: {
      'kernel-qc': {
        id: 'kernel-qc',
        env: 'prod',
        path: 'qc/batch-release-times.ipynb',
        status: 'idle',
        message: null,
        packages: [],
        execution_count: 4,
      },
    },
  }
  const controls = { delayMs: 0, overrides: new Map() }
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture')
    const env = url.searchParams.get('env') ?? 'prod'
    let body = ''
    for await (const chunk of req) body += chunk
    requests.push({
      method: req.method,
      path: url.pathname,
      env,
      authorization: req.headers.authorization,
      body,
    })
    res.setHeader('content-type', 'application/json')
    if (req.headers.authorization !== 'Bearer browser-smoke-user') {
      res
        .writeHead(401)
        .end(JSON.stringify({ detail: 'User identity required' }))
      return
    }
    if (controls.delayMs)
      await new Promise((resolve) => setTimeout(resolve, controls.delayMs))
    const override = controls.overrides.get(url.pathname)
    if (override) {
      res.setHeader('x-request-id', 'fixture-readiness-1129')
      res.writeHead(override.status ?? 200).end(JSON.stringify(override.body))
      return
    }
    if (
      url.pathname === '/api/v1/environments' &&
      environments.every((item) => deniedEnvironments.includes(item.env))
    ) {
      res.writeHead(403).end(JSON.stringify({ detail: 'Access denied' }))
      return
    }
    const page = { env, items: [], next_cursor: null }
    const ref = env === 'staging' ? 'candidate' : 'main'
    const overview = {
      env,
      asset_count: env === 'staging' ? 7 : 3,
      materialized_asset_count: 0,
      latest_materialization_at: null,
      incident_counts: {},
      freshness_counts: {
        fresh: 0,
        stale: 0,
        unknown: env === 'staging' ? 7 : 3,
      },
      run_status_counts: {},
      run_history_truncated: false,
      quality_checks: {
        status: 'unknown',
        counts: null,
        reason: 'No checks observed',
      },
    }
    const responses = {
      '/api/v1/action-availability': {
        env,
        actions: Object.fromEntries(
          [
            'v1_query_submit',
            'v1_query_explain',
            'v1_query_cancel',
            'v1_saved_query_create',
            'v1_saved_query_update',
            'v1_saved_query_delete',
            'v1_notebook_save',
          ].map((operation) => [operation, { allowed: true, reason: null }]),
        ),
      },
      '/api/v1/admin/settings': { version: 1, values: {} },
      '/api/v1/environments': {
        items: environments.filter(
          (item) => !deniedEnvironments.includes(item.env),
        ),
        default_environment: defaultEnvironment,
        promotion_source_environment: null,
      },
      '/api/v1/me': {
        subject: 'browser-smoke',
        email: null,
        principal_type: 'user',
        roles: ['reader'],
        permissions: Object.fromEntries(
          environments.map((item) => [
            item.env,
            deniedEnvironments.includes(item.env)
              ? []
              : ['service.read', 'run.read'],
          ]),
        ),
      },
      '/api/v1/readiness': {
        env,
        observed_at: '2026-10-19T11:00:00Z',
        correlation_id: 'fixture-readiness-1129',
        capabilities: {
          orchestration: [
            {
              state: 'ready',
              reason_code: 'ready',
              message: 'Configured code location is loaded.',
              remediation: '',
            },
          ],
          incidents: [
            {
              state: 'ready',
              reason_code: 'ready',
              message: 'Required storage schema is readable.',
              remediation: '',
            },
          ],
          query_preview: [
            {
              state: 'configured',
              reason_code: 'enforcement_unverified',
              message:
                'Local configuration is not proof of deployed preview enforcement.',
              remediation:
                'Verify deployment enforcement before production use.',
            },
          ],
        },
      },
      '/api/v1/overview': overview,
      '/api/v1/loads': {
        ...page,
        observed_at: '2026-10-19T11:00:00Z',
        truncated: false,
        limitations: [],
      },
      '/api/v1/services': {
        ...page,
        dagster_location: 'fixture-location',
        nessie_ref: ref,
      },
      '/api/v1/sources': {
        ...page,
        items: [
          {
            id: 'warehouse_orders',
            key: ['warehouse_orders'],
            description: null,
            compute_kind: 'dlt',
            relation: 'bronze.orders',
            last_materialization_at: null,
            last_run_id: null,
          },
        ],
      },
      '/api/v1/layers': page,
      '/api/v1/runs': page,
      '/api/v1/incidents': page,
      '/api/v1/assets': page,
      '/api/v1/jobs': page,
      '/api/v1/query/catalog': {
        env,
        nessie_ref: ref,
        engine: 'trino',
        catalogs: [
          {
            name: 'lake',
            truncated: false,
            schemas: [{ name: 'raw', tables: ['orders'] }],
          },
        ],
      },
      '/api/v1/query/catalog/objects': catalogObjects(url, env, ref),
      '/api/v1/query/refs': {
        env,
        items: [{ env, name: ref, catalog: 'lake' }],
      },
      '/api/v1/query/engines': {
        env,
        items: [{ id: 'trino', status: 'configured' }],
      },
      '/api/v1/queries/saved': page,
      '/api/v1/queries': page,
    }
    if (url.pathname === '/api/v1/queries' && req.method === 'POST') {
      const input = JSON.parse(body)
      if (input.sql.includes('denied')) {
        res.writeHead(403).end(JSON.stringify({ detail: 'Denied query' }))
        return
      }
      res.writeHead(201).end(
        JSON.stringify({
          id: 'query-7',
          env,
          nessie_ref: ref,
          engine: 'trino',
          evidence_available: true,
          status: 'completed',
          sql_hash: 'smoke-hash',
          created_at: '2026-10-10T10:00:00Z',
          updated_at: '2026-10-10T10:00:01Z',
          result: {
            columns: [{ name: 'answer', type: 'bigint' }],
            rows: [{ answer: 7 }],
            has_more: false,
          },
          error: null,
        }),
      )
      return
    }
    if (
      [
        '/api/v1/query/catalog/details',
        '/api/v1/query/catalog/sample',
        '/api/v1/query/catalog/search',
      ].includes(url.pathname)
    ) {
      const handled = catalogFixture(url, env, ref)
      res.writeHead(handled.status).end(JSON.stringify(handled.body))
      return
    }
    if (url.pathname.startsWith('/api/v1/notebooks')) {
      const handled = notebookFixture(
        req.method,
        url,
        body,
        env,
        ref,
        notebooks,
      )
      res
        .writeHead(handled.status)
        .end(handled.status === 204 ? '' : JSON.stringify(handled.body))
      return
    }
    const response = responses[url.pathname]
    if (!response) {
      res
        .writeHead(404)
        .end(
          JSON.stringify({ detail: `Unimplemented fixture ${url.pathname}` }),
        )
      return
    }
    res.end(JSON.stringify(response))
  })
  return new Promise((resolve, reject) => {
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      resolve({
        server,
        requests,
        controls,
        notebooks,
        url: `http://127.0.0.1:${address.port}`,
      })
    })
  })
}
