/**
 * The console snapshot: one hook that loads every collection the deck
 * needs through the shared live-resource cache, then polls on a 15s
 * cadence while the tab is visible. refresh() force-invalidates and
 * re-pulls everything (used after mutations).
 */
import { useCallback, useEffect, useMemo, useReducer } from 'react'

import { synthesizeSnapshot } from './demo'

import type {
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryDatasetPipeline,
  ObservatoryLogEvent,
  ObservatoryOperation,
  ObservatoryOverview,
  ObservatoryPublishingReadinessItem,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryService,
  ObservatoryTable,
} from '@/observatory/api/types'
import {
  getObservatoryAssetRecords,
  getObservatoryDatasetRecords,
  getObservatoryLogRecords,
  getObservatoryOperationRecords,
  getObservatoryOverview,
  getObservatoryPipelineRecords,
  getObservatoryPublishingReadinessDirect,
  getObservatoryQualityRecords,
  getObservatoryServices,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { loadCachedResource } from '@/lib/liveResource'

export interface Snapshot {
  assets: ObservatoryResourceResult<Array<ObservatoryAsset>>
  datasets: ObservatoryResourceResult<Array<ObservatoryDataset>>
  logs: ObservatoryResourceResult<Array<ObservatoryLogEvent>>
  operations: ObservatoryResourceResult<Array<ObservatoryOperation>>
  overview: ObservatoryResourceResult<ObservatoryOverview>
  pipelines: ObservatoryResourceResult<Array<ObservatoryDatasetPipeline>>
  publishing: ObservatoryResourceResult<
    Array<ObservatoryPublishingReadinessItem>
  >
  quality: ObservatoryResourceResult<Array<ObservatoryQualityCheck>>
  services: ObservatoryResourceResult<Array<ObservatoryService>>
  tables: ObservatoryResourceResult<Array<ObservatoryTable>>
  updatedAt: Date | null
}

type Field = keyof Omit<Snapshot, 'updatedAt'>

const LOADERS: Record<
  Field,
  () => Promise<ObservatoryResourceResult<unknown>>
> = {
  assets: getObservatoryAssetRecords,
  datasets: getObservatoryDatasetRecords,
  logs: getObservatoryLogRecords,
  operations: getObservatoryOperationRecords,
  overview: getObservatoryOverview,
  pipelines: getObservatoryPipelineRecords,
  publishing: getObservatoryPublishingReadinessDirect,
  quality: getObservatoryQualityRecords,
  services: getObservatoryServices,
  tables: getObservatoryTableRecords,
}

const STALE_MS: Record<Field, number> = {
  assets: 60_000,
  datasets: 60_000,
  logs: 15_000,
  operations: 15_000,
  overview: 30_000,
  pipelines: 60_000,
  publishing: 60_000,
  quality: 60_000,
  services: 60_000,
  tables: 60_000,
}

export function useLakehouseSnapshot(demoCount?: number | null) {
  const [snapshot, setSnapshot] = useReducer(
    (current: Snapshot, patch: Partial<Snapshot>): Snapshot => ({
      ...current,
      ...patch,
    }),
    {
      assets: { data: null, error: null },
      datasets: { data: null, error: null },
      logs: { data: null, error: null },
      operations: { data: null, error: null },
      overview: { data: null, error: null },
      pipelines: { data: null, error: null },
      publishing: { data: null, error: null },
      quality: { data: null, error: null },
      services: { data: null, error: null },
      tables: { data: null, error: null },
      updatedAt: null,
    },
  )

  const demo = useMemo(
    () => (demoCount ? synthesizeSnapshot(demoCount) : null),
    [demoCount],
  )

  const refresh = useCallback(() => {
    for (const [field, loader] of Object.entries(LOADERS) as Array<
      [Field, (typeof LOADERS)[Field]]
    >) {
      void loadCachedResource(`observatory:${field}`, loader, {
        force: true,
        staleMs: 0,
      }).then((next) => {
        setSnapshot({
          [field]: next,
          updatedAt: new Date(),
        } as Partial<Snapshot>)
      })
    }
  }, [])

  useEffect(() => {
    if (demo) return
    let cancelled = false
    function load() {
      for (const [field, loader] of Object.entries(LOADERS) as Array<
        [Field, (typeof LOADERS)[Field]]
      >) {
        void loadCachedResource(`observatory:${field}`, loader, {
          force: true,
          staleMs: STALE_MS[field],
        }).then((next) => {
          if (cancelled) return
          setSnapshot({
            [field]: next,
            updatedAt: new Date(),
          } as Partial<Snapshot>)
        })
      }
    }
    load()
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') load()
    }, 15_000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [demo])

  return { refresh, snapshot: demo ?? snapshot }
}
