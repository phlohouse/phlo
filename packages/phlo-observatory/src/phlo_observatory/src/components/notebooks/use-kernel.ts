/** This notebook's kernel: start it (installing packages first), run cells in the background, poll, stop. */
import * as React from 'react'
import type { CellRun, KernelView } from '@/lib/data/api/notebooks'
import {
  controlKernel,
  getCellRun,
  getKernel,
  startCellRun,
  startKernel,
} from '@/lib/data/api/notebooks'

export type KernelPhase =
  'disabled' | 'off' | 'installing' | 'starting' | 'failed' | 'idle' | 'busy'

export type CellWork = {
  cellId: string
  kind: 'python' | 'duckdb' | 'trino'
  source: string
  name: string | null
}

type Started = { id: string } | { error: string }

const RUN_POLL_MS = 300
const START_POLL_MS = 1000
const START_DEADLINE_MS = 30 * 60_000

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function phaseOf(kernel: KernelView): KernelPhase {
  return kernel.status
}

export function useKernel({
  env,
  path,
  existing,
  disabled,
  onLost,
}: {
  env: string
  path: string
  existing?: KernelView
  /** Why kernels are off here, if they are */
  disabled: string | null
  /** The kernel stopped under us; earlier results no longer exist in it */
  onLost: () => void
}) {
  const ref = React.useRef<KernelView | null>(existing ?? null)
  const [kernel, setKernel] = React.useState<KernelView | null>(
    existing ?? null,
  )
  const [phase, setPhase] = React.useState<KernelPhase>(
    disabled ? 'disabled' : existing ? phaseOf(existing) : 'off',
  )
  const [message, setMessage] = React.useState<string | null>(
    disabled ?? existing?.message ?? null,
  )
  const activeRun = React.useRef<string | null>(null)
  const alive = React.useRef(true)
  React.useEffect(
    () => () => {
      alive.current = false
    },
    [],
  )

  const adopt = React.useCallback((next: KernelView | null) => {
    ref.current = next
    setKernel(next)
    setPhase(next ? phaseOf(next) : 'off')
  }, [])

  /** Polls a kernel that is installing packages or starting until it is ready or failed. */
  const settle = React.useCallback(
    async (started: KernelView): Promise<KernelView | null> => {
      let current = started
      const deadline = Date.now() + START_DEADLINE_MS
      while (
        (current.status === 'installing' || current.status === 'starting') &&
        Date.now() < deadline &&
        alive.current
      ) {
        await wait(START_POLL_MS)
        const result = await getKernel({ data: { env, kernel: current.id } })
        if (result.state !== 'ok') return null
        current = result.data
        adopt(current)
      }
      return current
    },
    [adopt, env],
  )

  const ensure = React.useCallback(async (): Promise<Started> => {
    if (ref.current?.status === 'idle' || ref.current?.status === 'busy')
      return { id: ref.current.id }
    setPhase('starting')
    setMessage(null)
    try {
      const started = await startKernel({ data: { env, path } })
      if (started.state === 'disabled') {
        setPhase('disabled')
        setMessage(started.message)
        return { error: started.message }
      }
      if (started.state !== 'ok') {
        const text =
          'message' in started ? started.message : 'The kernel could not start.'
        adopt(null)
        setMessage(text)
        return { error: text }
      }
      adopt(started.data)
      const ready = await settle(started.data)
      if (!ready || ready.status !== 'idle') {
        const text = ready?.message ?? 'The kernel could not start.'
        setPhase('failed')
        setMessage(text)
        return { error: text }
      }
      return { id: ready.id }
    } catch (error) {
      adopt(null)
      const text =
        error instanceof Error ? error.message : 'The kernel could not start.'
      setMessage(text)
      return { error: text }
    }
  }, [adopt, env, path, settle])

  /** Follows a background run until it finishes, reporting output as it arrives. */
  const follow = React.useCallback(
    async (
      kernelId: string,
      runId: string,
      progress: (run: CellRun) => void,
    ): Promise<CellRun | string> => {
      activeRun.current = runId
      try {
        for (;;) {
          await wait(RUN_POLL_MS)
          const polled = await getCellRun({
            data: { env, kernel: kernelId, run: runId },
          })
          if (polled.state === 'gone')
            return 'The kernel stopped while the cell ran.'
          if (polled.state !== 'ok') continue
          if (polled.data.result) progress(polled.data.result)
          if (polled.data.status !== 'running')
            return polled.data.result ?? 'The cell finished without a result.'
        }
      } finally {
        activeRun.current = null
      }
    },
    [env],
  )

  /** Runs one cell, starting a fresh kernel once if the old one had stopped. */
  const run = React.useCallback(
    async (
      work: CellWork,
      progress: (run: CellRun) => void,
    ): Promise<CellRun | string> => {
      for (let attempt = 0; attempt < 2; attempt++) {
        const started = await ensure()
        if ('error' in started) return started.error
        const result = await startCellRun({
          data: {
            env,
            kernel: started.id,
            kind: work.kind,
            source: work.source,
            name: work.name,
            cell_id: work.cellId,
            path,
          },
        })
        if (result.state === 'gone') {
          adopt(null)
          onLost()
          setMessage(
            'The kernel had stopped, so a new one started. Earlier results were cleared.',
          )
          continue
        }
        if (result.state === 'disabled') {
          setPhase('disabled')
          setMessage(result.message)
          return result.message
        }
        if (result.state !== 'ok') return result.message
        setPhase('busy')
        try {
          return await follow(started.id, result.data.id, progress)
        } finally {
          if (alive.current)
            setPhase((current) => (current === 'busy' ? 'idle' : current))
        }
      }
      return 'The kernel is not running.'
    },
    [adopt, ensure, env, follow, onLost, path],
  )

  const control = React.useCallback(
    async (action: 'interrupt' | 'restart' | 'stop') => {
      const current = ref.current
      if (!current) return
      try {
        const result = await controlKernel({
          data: { env, kernel: current.id, action },
        })
        if (action === 'stop' || result.state === 'gone') adopt(null)
        else if (action === 'restart' && result.state === 'ok' && result.data)
          adopt(result.data)
        if (result.state === 'refused' || result.state === 'busy')
          setMessage(result.message)
      } catch (error) {
        setMessage(
          error instanceof Error
            ? error.message
            : 'The kernel did not respond.',
        )
      }
    },
    [adopt, env],
  )

  const kernelId = React.useCallback(() => ref.current?.id ?? null, [])
  return {
    kernel,
    phase,
    message,
    setMessage,
    start: ensure,
    run,
    control,
    kernelId,
    running: () => activeRun.current !== null,
  }
}
