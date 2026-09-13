/**
 * Synthetic lakehouse for scale testing: `?demo=N` renders an N-asset
 * system built as ~N/20 pipelines, each a small source→ingest→staging→
 * transform→publish DAG. Deterministic (seeded), so a link reproduces.
 */
import type { Snapshot } from './snapshot'
import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryPublishingReadinessItem,
  ObservatoryQualityCheck,
  ObservatoryService,
  ObservatoryTable,
} from '@/observatory/api/types'

/** mulberry32 — deterministic PRNG. */
function rng(seed: number) {
  return () => {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const PIPELINE_NAMES = [
  'orders',
  'payments',
  'telemetry',
  'clickstream',
  'inventory',
  'billing',
  'shipments',
  'accounts',
  'sessions',
  'pricing',
  'support',
  'marketing',
  'fraud',
  'catalog',
  'fulfilment',
]
const ENTITY_WORDS = [
  'events',
  'snapshots',
  'windows',
  'totals',
  'rollups',
  'features',
  'enriched',
  'validated',
  'joined',
  'deduped',
  'normalized',
  'scored',
]

function name(rand: () => number, index: number): string {
  const p = PIPELINE_NAMES[Math.floor(rand() * PIPELINE_NAMES.length)]
  const e = ENTITY_WORDS[Math.floor(rand() * ENTITY_WORDS.length)]
  return `${p}_${e}_${index}`
}

export function synthesizeSnapshot(count: number): Snapshot {
  const rand = rng(count)
  const assets: Array<ObservatoryAsset> = []
  const pipelines = Math.max(3, Math.round(count / 20))
  let index = 0

  for (let p = 0; p < pipelines && index < count; p += 1) {
    const source: ObservatoryAsset = {
      id: `src_${p}`,
      name: `src_${PIPELINE_NAMES[p % PIPELINE_NAMES.length]}_${p}`,
      dependencies: [],
      checks: [],
      group: 'source',
      kinds: ['source'],
      metadata: {},
      resources: [],
    }
    assets.push(source)
    index += 1

    let parents = [source.id]
    const tiers: Array<{ group: string; max: number; kinds: Array<string> }> = [
      { group: 'ingest', max: 2, kinds: ['dlt'] },
      { group: 'staging', max: 4, kinds: ['dbt', 'table'] },
      { group: 'transform', max: 8, kinds: ['dbt', 'table'] },
      { group: 'publish', max: 2, kinds: ['dbt', 'table'] },
    ]
    for (const tier of tiers) {
      const n = Math.min(
        1 + Math.floor(rand() * tier.max),
        count - index - (tier.group === 'publish' ? 0 : 4),
      )
      const tierIds: Array<string> = []
      for (let i = 0; i < n && index < count; i += 1) {
        const deps = parents.filter(() => rand() < 0.8)
        const asset: ObservatoryAsset = {
          id: `a_${index}`,
          name: name(rand, index),
          dependencies: deps.length ? deps : parents.slice(0, 1),
          checks: [],
          group: tier.group,
          kinds: tier.kinds,
          metadata: {},
          resources: [],
        }
        assets.push(asset)
        tierIds.push(asset.id)
        index += 1
      }
      if (tierIds.length) parents = tierIds
    }
  }
  // Top up to exactly `count` with singleton transforms.
  while (index < count) {
    assets.push({
      id: `a_${index}`,
      name: name(rand, index),
      dependencies: [assets[Math.floor(rand() * assets.length)].id],
      checks: [],
      group: 'transform',
      kinds: ['dbt'],
      metadata: {},
      resources: [],
    })
    index += 1
  }

  const datasets: Array<ObservatoryDataset> = []
  const datasetPipelines: Array<ObservatoryDatasetPipeline> = []
  const tables: Array<ObservatoryTable> = []
  const lakeOdds: Record<string, number> = {
    publish: 0.7,
    staging: 0.3,
    transform: 0.45,
  }
  for (const asset of assets) {
    const lake = lakeOdds[asset.group ?? '']
    if (!lake || rand() > lake) continue
    const readiness = rand() < 0.08 ? 'error' : rand() < 0.2 ? 'warning' : 'ok'
    const publication = rand() < 0.75 ? 'published' : 'draft'
    datasets.push({
      id: asset.id,
      name: asset.name,
      candidate: rand() < 0.12,
      classifications: [],
      kinds: asset.kinds,
      metadata: {},
      owner: 'data-eng',
      publication_state: publication,
      readiness_state: readiness,
      source_refs: [{ id: asset.id, kind: 'asset', label: asset.name }],
    })
    datasetPipelines.push({
      actions: [],
      dataset: datasets[datasets.length - 1],
      freshness_at: new Date(Date.now() - rand() * 20 * 36e5).toISOString(),
      freshness_state: readiness,
      last_run: null,
      stages: [],
    })
    if (asset.group === 'publish' && rand() < 0.7) {
      tables.push({
        id: `tbl_${asset.id}`,
        name: asset.name,
        asset_id: asset.id,
        branch: 'main',
        metadata: {},
        namespace: 'lake',
        schema_name: asset.group,
      })
    }
  }

  const quality: Array<ObservatoryQualityCheck> = []
  for (const asset of assets) {
    if (rand() > 0.3) continue
    const n = 1 + Math.floor(rand() * 3)
    for (let i = 0; i < n; i += 1) {
      const failing = rand() < 0.08
      quality.push({
        id: `chk_${asset.id}_${i}`,
        name: `${asset.name} · expectation_${i}`,
        asset_id: asset.id,
        blocking: failing && rand() < 0.4,
        metadata: {},
        severity: failing ? 'high' : 'medium',
        status: failing ? 'failing' : rand() < 0.06 ? 'warning' : 'passing',
      })
    }
  }

  const operations: Array<ObservatoryOperation> = []
  const opCount = Math.min(120, Math.max(20, Math.round(count / 8)))
  const flight = Math.max(1, Math.round(count / 400))
  // Ops land on dataset-producing assets more often than not — the
  // waterline should read as flow into the lake, not platform noise.
  const assetById = new Map(assets.map((asset) => [asset.id, asset]))
  const lakeAssetIds = datasets.map((d) => d.source_refs[0]?.id).filter(Boolean)
  for (let i = 0; i < opCount; i += 1) {
    const asset =
      lakeAssetIds.length > 0 && rand() < 0.7
        ? (assetById.get(
            lakeAssetIds[Math.floor(rand() * lakeAssetIds.length)],
          ) ?? assets[0])
        : assets[Math.floor(rand() * assets.length)]
    const status = i < 3 ? 'running' : rand() < 0.1 ? 'failed' : 'succeeded'
    const isPublish = status === 'succeeded' && rand() < 0.08
    const started = new Date(Date.now() - rand() * 22 * 36e5)
    const scope =
      i < flight * 3
        ? `pipeline-run-${Math.floor(i / 3)}`
        : status === 'failed'
          ? `pipeline-run-${i}`
          : undefined
    operations.push({
      id: `op_${i}`,
      name: isPublish
        ? 'publish dataset'
        : status === 'failed'
          ? 'WAP lifecycle'
          : 'materialize',
      completed_at:
        status === 'running'
          ? null
          : new Date(started.getTime() + rand() * 9e5).toISOString(),
      duration_seconds: Math.round(rand() * 900),
      health: {
        state: status === 'failed' ? 'error' : 'ok',
      },
      kind: isPublish ? 'dataset.publish' : 'materialize',
      metadata: scope ? { scope } : {},
      started_at: started.toISOString(),
      status,
      target: { id: asset.id, kind: 'asset', label: asset.name },
    })
  }

  const services: Array<ObservatoryService> = [
    'nessie',
    'minio',
    'trino',
    'dagster',
    'dagster-daemon',
    'postgres',
  ].map((id, i) => ({
    id,
    name: id,
    backend: 'docker',
    depends_on: [],
    health: { state: i === 5 && count > 500 ? 'warning' : 'ok' },
    impacts: [],
    in_stack: true,
    kind: 'infra',
    links: [],
    metadata: {},
    status: 'running',
  }))

  const logs: Array<ObservatoryLogEvent> = operations
    .filter((operation) => operation.status === 'failed')
    .slice(0, 12)
    .map((operation, i) => ({
      id: `log_${i}`,
      level: 'error',
      message: `materialization failed: upstream freshness gate rejected ${operation.target?.label}`,
      metadata: {},
      resource: operation.target,
      timestamp: operation.completed_at,
    }))

  const publishing: Array<ObservatoryPublishingReadinessItem> = datasets
    .filter((dataset) => !dataset.candidate)
    .map((dataset) => {
      const published = dataset.publication_state === 'published'
      const blockers =
        dataset.readiness_state === 'error'
          ? ['freshness gate rejected the last two runs']
          : []
      const warnings =
        dataset.readiness_state === 'warning'
          ? ['row count drifted 14% over trailing window']
          : []
      const missing =
        !published && blockers.length === 0 && rand() < 0.3
          ? ['no owner sign-off recorded']
          : []
      const state = blockers.length
        ? 'error'
        : warnings.length
          ? 'warning'
          : missing.length
            ? 'unknown'
            : 'ok'
      const publishable =
        !published &&
        (state === 'ok' || state === 'warning') &&
        missing.length === 0 &&
        rand() < 0.8
      return {
        dataset_id: dataset.id,
        publishing: {
          actions: [
            {
              consequences: [
                'Sets the Dataset publication state to published.',
              ],
              enabled: publishable,
              id: 'publish',
              label: 'Publish internally',
              reason: publishable
                ? null
                : published
                  ? 'This Dataset is already published.'
                  : 'Readiness policy has blockers.',
            },
            {
              consequences: ['Sets the Dataset publication state to retired.'],
              enabled: published,
              id: 'retire',
              label: 'Retire',
              reason: published
                ? null
                : 'Only published Datasets can be retired.',
            },
          ],
          blockers,
          internal_only: true,
          missing_evidence: missing,
          policy_name: 'default',
          state:
            state as ObservatoryPublishingReadinessItem['publishing']['state'],
          warnings,
        },
      }
    })

  const failed = operations.filter((o) => o.status === 'failed').length
  const failingChecks = quality.filter((c) => c.status === 'failing').length

  const ok = <T>(data: T) => ({ data, error: null })
  return {
    assets: ok(assets),
    datasets: ok(datasets),
    logs: ok(logs),
    operations: ok(operations),
    overview: ok({
      attention: [],
      counters: { assets: count, failed, failing_checks: failingChecks },
      events: [],
      health: {
        message: `${failed} failed runs · ${failingChecks} failing checks`,
        state: failed > 0 || failingChecks > 0 ? 'warning' : 'ok',
      },
      recent: [],
    }),
    pipelines: ok(datasetPipelines),
    publishing: ok(publishing),
    quality: ok(quality),
    services: ok(services),
    tables: ok(tables),
    updatedAt: new Date(),
  }
}
