// @vitest-environment jsdom
/** Exercises the catalog browser, the kernel menu and the new-notebook dialog. */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CatalogBrowser, qualifiedName, sqlIdentifier } from './catalog-browser'
import { KernelMenu } from './kernel-menu'
import { NewNotebookDialog } from './new-notebook'

const { objects, kernels, control } = vi.hoisted(() => ({
  objects: vi.fn(),
  kernels: vi.fn(),
  control: vi.fn(),
}))
vi.mock('@/lib/data/api/catalog', () => ({ getCatalogObjects: objects }))
vi.mock('@/lib/data/api/notebooks', () => ({
  listKernels: kernels,
  controlKernel: control,
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const item = (name: string, kind: string, data_type: string | null = null) => ({
  name,
  kind,
  data_type,
  nullable: data_type ? true : null,
})
const page = (
  items: Array<ReturnType<typeof item>>,
  next: number | null = null,
) => ({
  state: 'available',
  page: {
    env: 'prod',
    nessie_ref: 'main',
    catalog: 'lake',
    schema_name: null,
    table: null,
    items,
    next_offset: next,
  },
})

describe('catalog browser', () => {
  it('quotes identifiers only when it has to', () => {
    expect(sqlIdentifier('trials')).toBe('trials')
    expect(sqlIdentifier('Trial "Starts"')).toBe('"Trial ""Starts"""')
    expect(qualifiedName('lake', 'market', 'Q1')).toBe('lake.market."Q1"')
  })

  it('loads schemas, then tables and views, then columns as they open', async () => {
    objects.mockImplementation(({ data }) =>
      Promise.resolve(
        data.table
          ? page([item('nct_id', 'column', 'varchar')])
          : data.schema
            ? page([item('trials', 'table'), item('share', 'view')])
            : page([item('market', 'schema')]),
      ),
    )
    const onQuery = vi.fn()
    render(
      <CatalogBrowser env="prod" environments={['prod']} onQuery={onQuery} />,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Schema market' }),
    )
    expect(
      await screen.findByRole('button', { name: 'View market.share' }),
    ).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'Table market.trials' }),
    )
    expect(await screen.findByText('nct_id')).toBeTruthy()
    expect(screen.getByText('varchar')).toBeTruthy()
    expect(screen.getByText('main')).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'Query market.trials in a new cell' }),
    )
    expect(onQuery).toHaveBeenCalledWith('lake.market.trials', 'trials')
    expect(objects).toHaveBeenCalledWith({
      data: { env: 'prod', schema: 'market', table: 'trials', offset: 0 },
    })
  })

  it('pages with Show more and says once when the catalog is unreachable', async () => {
    objects
      .mockResolvedValueOnce(page([item('a', 'schema')], 100))
      .mockResolvedValueOnce(page([item('b', 'schema')]))
    render(
      <CatalogBrowser env="prod" environments={['prod']} onQuery={vi.fn()} />,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Show more' }),
    )
    expect(await screen.findByRole('button', { name: 'Schema b' })).toBeTruthy()
    expect(objects).toHaveBeenLastCalledWith({
      data: { env: 'prod', schema: undefined, table: undefined, offset: 100 },
    })

    cleanup()
    objects.mockReset()
    objects.mockResolvedValue({
      state: 'unavailable',
      message: 'Not reachable',
    })
    render(
      <CatalogBrowser env="prod" environments={['prod']} onQuery={vi.fn()} />,
    )
    expect((await screen.findByRole('alert')).textContent).toBe('Not reachable')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(objects).toHaveBeenCalledTimes(1)
  })
})

describe('kernel menu', () => {
  const handlers = () => ({
    onStart: vi.fn(),
    onRestart: vi.fn(),
    onInterrupt: vi.fn(),
    onStop: vi.fn(),
  })

  it('starts a stopped kernel and lists the other running kernels', async () => {
    kernels.mockResolvedValue({
      state: 'ok',
      data: [
        {
          id: 'k2',
          env: 'prod',
          path: 'qc/release.ipynb',
          status: 'idle',
          execution_count: 3,
        },
      ],
    })
    control.mockResolvedValue({ state: 'ok', data: null })
    const on = handlers()
    render(<KernelMenu env="prod" phase="off" kernelId={null} {...on} />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Kernel: Python 3 · stopped' }),
    )
    await userEvent.click(
      await screen.findByRole('menuitem', { name: /Start kernel/ }),
    )
    expect(on.onStart).toHaveBeenCalled()
    await userEvent.click(
      screen.getByRole('button', { name: 'Kernel: Python 3 · stopped' }),
    )
    await userEvent.click(
      await screen.findByRole('menuitem', {
        name: 'Stop the kernel for qc/release.ipynb',
      }),
    )
    await waitFor(() =>
      expect(control).toHaveBeenCalledWith({
        data: { env: 'prod', kernel: 'k2', action: 'stop' },
      }),
    )
  })

  it('offers interrupt while busy and explains when kernels are off', async () => {
    kernels.mockResolvedValue({ state: 'ok', data: [] })
    const on = handlers()
    render(<KernelMenu env="prod" phase="busy" kernelId="k1" {...on} />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Kernel: Python 3 · busy' }),
    )
    await userEvent.click(
      await screen.findByRole('menuitem', { name: /Interrupt/ }),
    )
    expect(on.onInterrupt).toHaveBeenCalled()

    cleanup()
    render(
      <KernelMenu
        env="prod"
        phase="disabled"
        kernelId={null}
        {...handlers()}
      />,
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Kernel: Kernels off' }),
    )
    expect(await screen.findByText(/PHLO_NOTEBOOK_KERNELS=1/)).toBeTruthy()
    expect(kernels).toHaveBeenCalledTimes(1)
  })
})

describe('new notebook dialog', () => {
  it('creates a notebook at the folder and name given', async () => {
    const onCreate = vi.fn().mockResolvedValue(null)
    const onOpenChange = vi.fn()
    render(
      <NewNotebookDialog
        open
        onOpenChange={onOpenChange}
        folders={['market']}
        onCreate={onCreate}
      />,
    )
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Notebook name' }),
      'Q1 review',
    )
    await userEvent.type(
      screen.getByRole('combobox', { name: 'Folder' }),
      'market',
    )
    expect(screen.getByText(/notebooks\/market\//)).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'Create notebook' }),
    )
    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(onCreate.mock.calls[0][0]).toMatch(/^market\/.+\.ipynb$/)
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('keeps the dialog open with the reason when the file already exists', async () => {
    const onCreate = vi
      .fn()
      .mockResolvedValue('A notebook with that name exists.')
    const onOpenChange = vi.fn()
    render(
      <NewNotebookDialog
        open
        onOpenChange={onOpenChange}
        folders={[]}
        onCreate={onCreate}
      />,
    )
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Notebook name' }),
      'trials',
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Create notebook' }),
    )
    expect((await screen.findByRole('alert')).textContent).toBe(
      'A notebook with that name exists.',
    )
    expect(onOpenChange).not.toHaveBeenCalled()
  })
})
