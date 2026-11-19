/** Reads and saves project notebooks and drives their Jupyter kernels through the Phlo API. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { PhloApiError, environmentSchema, phloApi } from './client'

const resultName = z.string().regex(/^[A-Za-z_][A-Za-z0-9_]{0,63}$/)
export const notebookPathSchema = z
  .string()
  .min(1)
  .max(512)
  .regex(/^[A-Za-z0-9_][A-Za-z0-9_.-]*(\/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\.ipynb$/)

const cellSchema = z.object({
  id: z.string().min(1).max(64),
  kind: z.enum(['markdown', 'python', 'sql']),
  source: z.string().max(256 * 1024),
  name: resultName.nullable(),
  engine: z.enum(['trino', 'duckdb']).nullable(),
})
const notebookSchema = z.object({
  env: environmentSchema,
  path: z.string(),
  digest: z.string(),
  nessie_ref: z.string(),
  cells: z.array(cellSchema),
})
const listSchema = z.object({
  env: environmentSchema,
  root: z.string(),
  items: z.array(
    z.object({ path: z.string(), size: z.number(), modified_at: z.string() }),
  ),
  truncated: z.boolean(),
})
const kernelSchema = z.object({
  id: z.string(),
  env: environmentSchema,
  path: z.string(),
  status: z.enum(['idle', 'busy']),
  execution_count: z.number().int(),
})
const tableSchema = z.object({
  columns: z.array(z.object({ name: z.string(), type: z.string().nullable() })),
  rows: z.array(z.record(z.string(), z.json())),
  row_count: z.number().int().nonnegative(),
})
const runSchema = z.object({
  status: z.enum(['ok', 'error', 'timeout']),
  execution_count: z.number().int().nullable(),
  duration_ms: z.number().int().nonnegative(),
  outputs: z.array(
    z.object({
      type: z.enum(['stream', 'result', 'image', 'error']),
      text: z.string().nullable(),
      name: z.string().nullable(),
      png: z.string().nullable(),
      ename: z.string().nullable(),
    }),
  ),
  truncated: z.boolean(),
  table: tableSchema.nullable(),
})
const variablesSchema = z.object({
  items: z.array(
    z.object({
      name: z.string(),
      type: z.string(),
      shape: z.string().nullable(),
      preview: z.string().nullable(),
    }),
  ),
})

export type NotebookCellData = z.infer<typeof cellSchema>
export type NotebookDocument = z.infer<typeof notebookSchema>
export type NotebookSummary = z.infer<typeof listSchema>['items'][number]
export type KernelView = z.infer<typeof kernelSchema>
export type CellRun = z.infer<typeof runSchema>
export type CellTable = z.infer<typeof tableSchema>
export type KernelVariable = z.infer<typeof variablesSchema>['items'][number]

const envInput = z.object({ env: environmentSchema })
const pathInput = envInput.extend({ path: notebookPathSchema })
const kernelInput = envInput.extend({ kernel: z.string().min(1).max(64) })

const kernelsOff =
  'Notebook kernels are off. An operator can turn them on with PHLO_NOTEBOOK_KERNELS=1 on a single-process API.'

/** Loads the notebook list, the open notebook and whether kernels can run here. */
export const getNotebookWorkspace = createServerFn({ method: 'GET' })
  .inputValidator(envInput.extend({ path: notebookPathSchema.optional() }))
  .handler(async ({ data }) => {
    const env = data.env
    const list = await phloApi(`/api/v1/notebooks?env=${env}`, listSchema, {
      env,
    })
    let kernels:
      | { state: 'available'; items: Array<KernelView> }
      | { state: 'disabled'; message: string }
    try {
      kernels = {
        state: 'available',
        items: await phloApi(
          `/api/v1/notebooks/kernels?env=${env}`,
          z.array(kernelSchema),
          { env },
        ),
      }
    } catch (error) {
      if (!(error instanceof PhloApiError) || error.status !== 503) throw error
      kernels = { state: 'disabled', message: kernelsOff }
    }
    const path = data.path ?? list.items[0]?.path
    let notebook:
      | { state: 'none' }
      | { state: 'open'; document: NotebookDocument }
      | { state: 'unreadable'; path: string; message: string } = {
      state: 'none',
    }
    if (path) {
      try {
        notebook = {
          state: 'open',
          document: await phloApi(
            `/api/v1/notebooks/content?env=${env}&path=${encodeURIComponent(path)}`,
            notebookSchema,
            { env },
          ),
        }
      } catch (error) {
        if (
          !(error instanceof PhloApiError) ||
          ![400, 404].includes(error.status)
        )
          throw error
        notebook = {
          state: 'unreadable',
          path,
          message:
            error.status === 404
              ? 'This notebook does not exist.'
              : 'This file is not a notebook Phlo can open. Only nbformat 4 notebooks up to 2 MB are supported.',
        }
      }
    }
    return { list, kernels, notebook }
  })

/** Saves cells. A changed file on disk comes back as a conflict, never an overwrite. */
export const saveNotebook = createServerFn({ method: 'POST' })
  .inputValidator(
    pathInput.extend({
      cells: z.array(cellSchema).max(500),
      expected_digest: z
        .string()
        .regex(/^[a-f0-9]{64}$/)
        .nullable(),
    }),
  )
  .handler(async ({ data }) => {
    try {
      return {
        state: 'saved' as const,
        document: await phloApi(
          `/api/v1/notebooks/content?env=${data.env}&path=${encodeURIComponent(data.path)}`,
          notebookSchema,
          {
            env: data.env,
            method: 'PUT',
            body: { cells: data.cells, expected_digest: data.expected_digest },
          },
        ),
      }
    } catch (error) {
      if (error instanceof PhloApiError && error.status === 409)
        return { state: 'conflict' as const }
      throw error
    }
  })

/** Kernel calls report a stopped or busy kernel as data so the page can recover. */
async function kernelCall<T>(
  request: () => Promise<T>,
): Promise<
  | { state: 'ok'; data: T }
  | { state: 'gone' }
  | { state: 'busy' }
  | { state: 'disabled'; message: string }
> {
  try {
    return { state: 'ok', data: await request() }
  } catch (error) {
    if (!(error instanceof PhloApiError)) throw error
    if (error.status === 404) return { state: 'gone' }
    if (error.status === 409) return { state: 'busy' }
    if (error.status === 503) return { state: 'disabled', message: kernelsOff }
    throw error
  }
}

export const startKernel = createServerFn({ method: 'POST' })
  .inputValidator(pathInput)
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(`/api/v1/notebooks/kernels?env=${data.env}`, kernelSchema, {
        env: data.env,
        method: 'POST',
        body: { path: data.path },
      }),
    ),
  )

export const runPython = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({
      code: z.string().max(256 * 1024),
      name: resultName.nullable(),
    }),
  )
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/execute?env=${data.env}`,
        runSchema,
        {
          env: data.env,
          method: 'POST',
          body: { code: data.code, name: data.name },
          timeoutMs: 150_000,
        },
      ),
    ),
  )

export const runDuckdb = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({
      sql: z
        .string()
        .min(1)
        .max(64 * 1024),
      name: resultName,
    }),
  )
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/sql?env=${data.env}`,
        runSchema,
        {
          env: data.env,
          method: 'POST',
          body: { sql: data.sql, name: data.name },
          timeoutMs: 150_000,
        },
      ),
    ),
  )

/** Loads a finished lakehouse query's rows into the kernel under `name`. */
export const bindQueryResult = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({
      query_id: z.string().min(1).max(64),
      name: resultName,
    }),
  )
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/bind?env=${data.env}`,
        runSchema,
        {
          env: data.env,
          method: 'POST',
          body: { query_id: data.query_id, name: data.name },
          timeoutMs: 150_000,
        },
      ),
    ),
  )

export const getKernelVariables = createServerFn({ method: 'GET' })
  .inputValidator(kernelInput)
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/variables?env=${data.env}`,
        variablesSchema,
        {
          env: data.env,
        },
      ),
    ),
  )

export const controlKernel = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({ action: z.enum(['interrupt', 'restart', 'stop']) }),
  )
  .handler(({ data }) =>
    kernelCall(async () => {
      if (data.action === 'stop') {
        await phloApi(
          `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}?env=${data.env}`,
          z.string(),
          { env: data.env, method: 'DELETE', responseType: 'text' },
        )
        return null
      }
      if (data.action === 'interrupt')
        return phloApi(
          `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/interrupt?env=${data.env}`,
          kernelSchema,
          { env: data.env, method: 'POST' },
        )
      return phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/restart?env=${data.env}`,
        kernelSchema,
        { env: data.env, method: 'POST' },
      )
    }),
  )
