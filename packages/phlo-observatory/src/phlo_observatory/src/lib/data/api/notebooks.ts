/** Reads, saves and manages project notebooks and drives their Jupyter kernels through the Phlo API. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { PhloApiError, environmentSchema, phloApi } from './client'

const resultName = z.string().regex(/^[A-Za-z_][A-Za-z0-9_]{0,63}$/)
export const notebookPathSchema = z
  .string()
  .min(1)
  .max(512)
  .regex(/^[A-Za-z0-9_][A-Za-z0-9_.-]*(\/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\.ipynb$/)
const digestSchema = z.string().regex(/^[a-f0-9]{64}$/)

const cellSchema = z.object({
  id: z.string().min(1).max(64),
  kind: z.enum(['markdown', 'python', 'sql']),
  source: z.string().max(256 * 1024),
  name: resultName.nullable(),
  engine: z.enum(['trino', 'duckdb']).nullable(),
})
const scheduleSchema = z.object({
  cron: z.string(),
  env: environmentSchema,
  owner: z.string(),
  enabled: z.boolean(),
})
const notebookSchema = z.object({
  env: environmentSchema,
  path: z.string(),
  digest: z.string(),
  nessie_ref: z.string(),
  cells: z.array(cellSchema),
  packages: z.array(z.string()),
  schedule: scheduleSchema.nullable(),
  access: z.object({
    edit: z.boolean(),
    run: z.boolean(),
    schedule: z.boolean(),
    publish: z.boolean(),
  }),
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
  status: z.enum(['installing', 'starting', 'failed', 'idle', 'busy']),
  message: z.string().nullable(),
  packages: z.array(z.string()),
  execution_count: z.number().int(),
})
const tableSchema = z.object({
  columns: z.array(z.object({ name: z.string(), type: z.string().nullable() })),
  rows: z.array(z.record(z.string(), z.json())),
  row_count: z.number().int().nonnegative(),
})
const runStatus = z.enum(['running', 'ok', 'error', 'timeout', 'cancelled'])
const runSchema = z.object({
  status: runStatus,
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
const runViewSchema = z.object({
  id: z.string(),
  kernel: z.string(),
  status: runStatus,
  result: runSchema.nullable(),
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
const packagesSchema = z.object({
  requested: z.array(z.string()),
  items: z.array(
    z.object({
      name: z.string(),
      version: z.string(),
      source: z.enum(['notebook', 'base']),
    }),
  ),
})
const completionsSchema = z.object({
  matches: z.array(z.string()),
  cursor_start: z.number().int(),
  cursor_end: z.number().int(),
})
const outputsSchema = z.object({
  items: z.array(
    z.object({
      cell_id: z.string(),
      source_sha256: z.string(),
      actor: z.string(),
      finished_at: z.string(),
      run: runSchema,
    }),
  ),
  retention_days: z.number().int(),
})

export type NotebookCellData = z.infer<typeof cellSchema>
export type NotebookDocument = z.infer<typeof notebookSchema>
export type NotebookSchedule = z.infer<typeof scheduleSchema>
export type NotebookAccess = NotebookDocument['access']
export type KernelView = z.infer<typeof kernelSchema>
export type CellRun = z.infer<typeof runSchema>
export type CellTable = z.infer<typeof tableSchema>
export type CellRunView = z.infer<typeof runViewSchema>
export type KernelVariable = z.infer<typeof variablesSchema>['items'][number]
export type KernelPackages = z.infer<typeof packagesSchema>
export type SavedOutput = z.infer<typeof outputsSchema>['items'][number]

const envInput = z.object({ env: environmentSchema })
const pathInput = envInput.extend({ path: notebookPathSchema })
const kernelInput = envInput.extend({ kernel: z.string().min(1).max(64) })

const kernelsOff =
  'Notebook kernels are off. An operator can turn them on with PHLO_NOTEBOOK_KERNELS=1 on a single-process API.'

function kernelsDisabled(error: PhloApiError) {
  return (
    error.status === 503 &&
    (!error.reasonCode || error.reasonCode === 'notebook_kernels_disabled')
  )
}

/** The server's reason when it gave one, else a plain fallback. */
export function reason(error: unknown, fallback: string) {
  if (error instanceof PhloApiError) return error.detail ?? fallback
  return fallback
}

async function readOutputs(env: string, path: string) {
  try {
    return await phloApi(
      `/api/v1/notebooks/outputs?env=${env}&path=${encodeURIComponent(path)}`,
      outputsSchema,
      { env },
    )
  } catch (error) {
    if (error instanceof PhloApiError && [401, 403].includes(error.status))
      throw error
    // Saved outputs are a convenience; the notebook still opens without them.
    return { items: [], retention_days: 0 }
  }
}

async function readKernels(env: string) {
  try {
    return {
      state: 'available' as const,
      items: await phloApi(
        `/api/v1/notebooks/kernels?env=${env}`,
        z.array(kernelSchema),
        { env },
      ),
    }
  } catch (error) {
    if (!(error instanceof PhloApiError) || !kernelsDisabled(error)) throw error
    return { state: 'disabled' as const, message: error.detail ?? kernelsOff }
  }
}

function unreadable(status: number) {
  if (status === 404) return 'This notebook does not exist.'
  if (status === 400) return 'That is not a notebook path Phlo can open.'
  return 'This file is not a notebook Phlo can open. Only nbformat 4 notebooks up to 2 MB are supported.'
}

async function readNotebook(env: string, path: string) {
  try {
    const [document, outputs] = await Promise.all([
      phloApi(
        `/api/v1/notebooks/content?env=${env}&path=${encodeURIComponent(path)}`,
        notebookSchema,
        { env },
      ),
      readOutputs(env, path),
    ])
    return { state: 'open' as const, document, outputs }
  } catch (error) {
    if (
      !(error instanceof PhloApiError) ||
      ![400, 404, 422].includes(error.status)
    )
      throw error
    return {
      state: 'unreadable' as const,
      path,
      message: unreadable(error.status),
    }
  }
}

/** Loads the notebook list, the open notebook with its saved outputs, and the kernels. */
export const getNotebookWorkspace = createServerFn({ method: 'GET' })
  .inputValidator(envInput.extend({ path: notebookPathSchema.optional() }))
  .handler(async ({ data }) => {
    const env = data.env
    const [list, kernels] = await Promise.all([
      phloApi(`/api/v1/notebooks?env=${env}`, listSchema, { env }),
      readKernels(env),
    ])
    const path = data.path ?? list.items[0]?.path
    const notebook = path
      ? await readNotebook(env, path)
      : { state: 'none' as const }
    return { list, kernels, notebook }
  })

/** Saves cells and packages. A changed file on disk comes back as a conflict, never an overwrite. */
export const saveNotebook = createServerFn({ method: 'POST' })
  .inputValidator(
    pathInput.extend({
      cells: z.array(cellSchema).max(500),
      packages: z.array(z.string().max(200)).max(50).default([]),
      expected_digest: digestSchema.nullable(),
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
            body: {
              cells: data.cells,
              packages: data.packages,
              expected_digest: data.expected_digest,
            },
          },
        ),
      }
    } catch (error) {
      if (error instanceof PhloApiError && error.status === 409)
        return { state: 'conflict' as const }
      if (error instanceof PhloApiError && error.status === 422)
        return {
          state: 'invalid' as const,
          message: reason(error, 'The notebook could not be saved.'),
        }
      throw error
    }
  })

type Outcome<T> =
  { state: 'ok'; data: T } | { state: 'refused'; message: string }

/** Turns refusals the person can act on (conflict, invalid, forbidden, off) into data. */
async function outcome<T>(
  request: () => Promise<T>,
  fallback: string,
): Promise<Outcome<T>> {
  try {
    return { state: 'ok', data: await request() }
  } catch (error) {
    if (
      error instanceof PhloApiError &&
      [400, 403, 404, 409, 422, 503].includes(error.status)
    )
      return { state: 'refused', message: reason(error, fallback) }
    throw error
  }
}

async function currentDigest(env: string, path: string, known?: string) {
  if (known) return known
  const document = await phloApi(
    `/api/v1/notebooks/content?env=${env}&path=${encodeURIComponent(path)}`,
    notebookSchema,
    { env },
  )
  return document.digest
}

/** Renames or moves a notebook; saved outputs and run history follow it. */
export const moveNotebook = createServerFn({ method: 'POST' })
  .inputValidator(
    pathInput.extend({
      to: notebookPathSchema,
      /** The version the page has open; left out for a notebook that is not open */
      expected_digest: digestSchema.optional(),
    }),
  )
  .handler(({ data }) =>
    outcome(async () => {
      const digest = await currentDigest(
        data.env,
        data.path,
        data.expected_digest,
      )
      return phloApi(
        `/api/v1/notebooks/move?env=${data.env}&path=${encodeURIComponent(data.path)}`,
        notebookSchema,
        {
          env: data.env,
          method: 'POST',
          body: { to: data.to, expected_digest: digest },
        },
      )
    }, 'A notebook already exists there, or this one changed since you opened it.'),
  )

/** Copies the saved notebook (cells and packages, not its schedule) to a new path. */
export const duplicateNotebook = createServerFn({ method: 'POST' })
  .inputValidator(pathInput.extend({ to: notebookPathSchema }))
  .handler(({ data }) =>
    outcome(async () => {
      const source = await phloApi(
        `/api/v1/notebooks/content?env=${data.env}&path=${encodeURIComponent(data.path)}`,
        notebookSchema,
        { env: data.env },
      )
      return phloApi(
        `/api/v1/notebooks/content?env=${data.env}&path=${encodeURIComponent(data.to)}`,
        notebookSchema,
        {
          env: data.env,
          method: 'PUT',
          body: {
            cells: source.cells,
            packages: source.packages,
            expected_digest: null,
          },
        },
      )
    }, 'A notebook with that name already exists.'),
  )

export const deleteNotebook = createServerFn({ method: 'POST' })
  .inputValidator(pathInput.extend({ digest: digestSchema.optional() }))
  .handler(({ data }) =>
    outcome(async () => {
      const digest = await currentDigest(data.env, data.path, data.digest)
      return phloApi(
        `/api/v1/notebooks/content?env=${data.env}&path=${encodeURIComponent(data.path)}&digest=${digest}`,
        z.string(),
        { env: data.env, method: 'DELETE', responseType: 'text' },
      )
    }, 'This notebook changed since you opened it. Reload it first.'),
  )

/** Kernel calls report a stopped or busy kernel as data so the page can recover. */
async function kernelCall<T>(
  request: () => Promise<T>,
): Promise<
  | { state: 'ok'; data: T }
  | { state: 'gone' }
  | { state: 'busy'; message: string }
  | { state: 'refused'; message: string }
  | { state: 'disabled'; message: string }
> {
  try {
    return { state: 'ok', data: await request() }
  } catch (error) {
    if (!(error instanceof PhloApiError)) throw error
    if (error.status === 404) return { state: 'gone' }
    if (error.status === 409)
      return {
        state: 'busy',
        message: reason(error, 'The kernel is still running another cell.'),
      }
    if (kernelsDisabled(error))
      return { state: 'disabled', message: error.detail ?? kernelsOff }
    if ([403, 422, 503].includes(error.status))
      return { state: 'refused', message: reason(error, 'The kernel refused.') }
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
        timeoutMs: 60_000,
      }),
    ),
  )

/** Starts a cell in the background; poll `getCellRun` for its output. */
export const startCellRun = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({
      kind: z.enum(['python', 'duckdb', 'trino']),
      source: z.string().max(256 * 1024),
      name: resultName.nullable(),
      cell_id: z.string().min(1).max(64),
      path: notebookPathSchema,
    }),
  )
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/runs?env=${data.env}`,
        runViewSchema,
        {
          env: data.env,
          method: 'POST',
          body: {
            kind: data.kind,
            source: data.source,
            name: data.name,
            cell_id: data.cell_id,
            path: data.path,
          },
        },
      ),
    ),
  )

export const getCellRun = createServerFn({ method: 'GET' })
  .inputValidator(kernelInput.extend({ run: z.string().min(1).max(64) }))
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/runs/${encodeURIComponent(data.run)}?env=${data.env}`,
        runViewSchema,
        { env: data.env },
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
        { env: data.env },
      ),
    ),
  )

export const getKernelPackages = createServerFn({ method: 'GET' })
  .inputValidator(kernelInput)
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/packages?env=${data.env}`,
        packagesSchema,
        { env: data.env, timeoutMs: 30_000 },
      ),
    ),
  )

/** Python completions from the live kernel; empty while it runs a cell. */
export const completePython = createServerFn({ method: 'POST' })
  .inputValidator(
    kernelInput.extend({
      code: z.string().max(256 * 1024),
      cursor: z.number().int().min(0),
    }),
  )
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels/${encodeURIComponent(data.kernel)}/complete?env=${data.env}`,
        completionsSchema,
        {
          env: data.env,
          method: 'POST',
          body: { code: data.code, cursor: data.cursor },
          timeoutMs: 10_000,
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
        { env: data.env, method: 'POST', timeoutMs: 60_000 },
      )
    }),
  )

/** The caller's running kernels in this environment, across notebooks. */
export const listKernels = createServerFn({ method: 'GET' })
  .inputValidator(envInput)
  .handler(({ data }) =>
    kernelCall(() =>
      phloApi(
        `/api/v1/notebooks/kernels?env=${data.env}`,
        z.array(kernelSchema),
        { env: data.env },
      ),
    ),
  )

/** One kernel's state, used while it installs packages and starts. */
export const getKernel = createServerFn({ method: 'GET' })
  .inputValidator(kernelInput)
  .handler(({ data }) =>
    kernelCall(async () => {
      const items = await phloApi(
        `/api/v1/notebooks/kernels?env=${data.env}`,
        z.array(kernelSchema),
        { env: data.env },
      )
      const found = items.find((item) => item.id === data.kernel)
      if (!found) throw new PhloApiError(404)
      return found
    }),
  )

export { outcome, notebookSchema, scheduleSchema, pathInput, digestSchema }
