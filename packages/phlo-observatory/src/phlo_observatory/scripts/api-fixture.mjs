/** Controlled HTTP fixture for the built-app smoke, never live-service evidence. */
import { createServer } from 'node:http'

export function startApiFixture() {
  const requests = []
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
      '/api/v1/me': {
        subject: 'browser-smoke',
        email: null,
        principal_type: 'user',
        roles: ['reader'],
        permissions: { staging: ['service.read', 'run.read'] },
      },
      '/api/v1/overview': overview,
      '/api/v1/services': {
        ...page,
        dagster_location: 'fixture-location',
        nessie_ref: ref,
      },
      '/api/v1/sources': page,
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
      resolve({ server, requests, url: `http://127.0.0.1:${address.port}` })
    })
  })
}
