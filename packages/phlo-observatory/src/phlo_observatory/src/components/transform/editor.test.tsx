// @vitest-environment jsdom
/** Source edits remain local while immutable revision compilation uses its saved identity. */
import {
  act,
  cleanup,
  render as renderComponent,
  screen,
  waitFor,
} from '@testing-library/react'
import {
  RouterContextProvider,
  createMemoryHistory,
  createRouter,
} from '@tanstack/react-router'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TransformEditor } from './editor'
import type { TransformModel } from '@/lib/data/api/transform'
import type { TransformDraftSummary } from '@/lib/data/api/transform-editor'
import type { ReactElement } from 'react'
import { routeTree } from '@/routeTree.gen'

function render(element: ReactElement) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({
      initialEntries: ['/transform?env=sandbox&model=review.inspection'],
    }),
  })
  return {
    ...renderComponent(element, {
      wrapper: ({ children }) => (
        <RouterContextProvider router={router}>
          {children}
        </RouterContextProvider>
      ),
    }),
    router,
  }
}

const api = vi.hoisted(() => ({
  source: vi.fn(),
  read: vi.fn(),
  save: vi.fn(),
  compile: vi.fn(),
  executions: vi.fn(),
  history: vi.fn(),
  compare: vi.fn(),
}))
vi.mock('@/lib/data/api/transform-editor', () => ({
  getTransformSource: api.source,
  getTransformDraft: api.read,
  saveTransformDraft: api.save,
  compileTransformDraft: api.compile,
  getTransformExecutions: api.executions,
  getTransformDrafts: api.history,
  compareTransformDraft: api.compare,
}))
vi.mock('@/components/query/sql-editor', () => ({
  SqlEditor: ({
    value,
    onChange,
    label,
  }: {
    value: string
    onChange: (value: string) => void
    label: string
  }) => (
    <textarea
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))
vi.mock('@/components/phlo/page', () => ({
  PageHeader: ({
    title,
    meta,
    actions,
  }: {
    title: string
    meta: string
    actions: React.ReactNode
  }) => (
    <header>
      <h1>{title}</h1>
      <p>{meta}</p>
      {actions}
    </header>
  ),
}))
const model: TransformModel = {
  asset_id: 'review/inspection',
  key: ['review', 'inspection'],
  model: 'review.inspection',
  path: 'transforms/inspection.sql',
  dependencies: [['gold', 'batches']],
}
const source = {
  model: model.model,
  env: 'sandbox',
  path: model.path,
  sql: 'SELECT * FROM gold.batches',
  source_digest: 'a'.repeat(64),
  workspace_digest: 'b'.repeat(64),
  model_version: 'actual',
}
const draft = {
  ...source,
  ref: 'main',
  draft_id: 'dd0d4fda-a546-4f4f-88d3-0cd12f495365',
  revision: 1,
  revision_digest: 'c'.repeat(64),
  sql_digest: 'd'.repeat(64),
  created_at: '',
}
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  vi.restoreAllMocks()
})
function setup(extra = {}, history: Array<TransformDraftSummary> = []) {
  api.history.mockResolvedValue({
    items: history,
    total: history.length,
    truncated: false,
  })
  api.executions.mockResolvedValue({ items: [] })
  api.source.mockResolvedValue(source)
  return render(
    <TransformEditor
      env="sandbox"
      model={model}
      focus={false}
      onFocus={vi.fn()}
      onClose={vi.fn()}
      onSaved={vi.fn()}
      {...extra}
    />,
  )
}

describe('Transform source editor', () => {
  it.each(['Save draft revision', 'Compile and plan'])(
    'ignores a late %s response after selecting another revision',
    async (action) => {
      api.read.mockImplementation(({ data }: { data: { revision: number } }) =>
        Promise.resolve({
          ...draft,
          revision: data.revision,
          sql: `SELECT ${data.revision}`,
        }),
      )
      let finish!: (value: unknown) => void
      const pending = new Promise((resolve) => {
        finish = resolve
      })
      api.save.mockReturnValue(pending)
      api.compile.mockReturnValue(pending)
      const onSaved = vi.fn()
      const props = {
        env: 'sandbox',
        model,
        focus: false,
        draftId: draft.draft_id,
        revision: 1,
        onFocus: vi.fn(),
        onClose: vi.fn(),
        onSaved,
      }
      const view = setup(props)
      const input = await screen.findByLabelText<HTMLTextAreaElement>(
        'SQL source for review.inspection',
      )
      if (action === 'Save draft revision')
        await userEvent.type(input, ' -- saved')
      await userEvent.click(screen.getByRole('button', { name: action }))
      view.rerender(<TransformEditor {...props} revision={2} />)
      await waitFor(() =>
        expect(
          screen.getByLabelText<HTMLTextAreaElement>(
            'SQL source for review.inspection',
          ).value,
        ).toBe('SELECT 2'),
      )
      await act(() =>
        finish({
          ...draft,
          sql: 'SELECT 1',
          status: 'ready',
          diagnostics: [],
          compiled_sql: 'OLD PLAN',
        }),
      )
      expect(
        screen.getByLabelText<HTMLTextAreaElement>(
          'SQL source for review.inspection',
        ).value,
      ).toBe('SELECT 2')
      expect(
        screen.queryByRole('region', { name: 'Compiled model SQL' }),
      ).toBeNull()
      expect(onSaved).not.toHaveBeenCalled()
    },
  )
  it('follows external revision changes and blocks a dirty history exit until discard is confirmed', async () => {
    api.read.mockImplementation(({ data }: { data: { revision: number } }) =>
      Promise.resolve({
        ...draft,
        revision: data.revision,
        sql: `SELECT ${data.revision}`,
      }),
    )
    const props = {
      env: 'sandbox',
      model,
      focus: false,
      draftId: draft.draft_id,
      revision: 1,
      onFocus: vi.fn(),
      onClose: vi.fn(),
      onSaved: vi.fn(),
    }
    const view = setup(props)
    expect(
      (
        await screen.findByLabelText<HTMLTextAreaElement>(
          'SQL source for review.inspection',
        )
      ).value,
    ).toBe('SELECT 1')
    view.rerender(<TransformEditor {...props} revision={2} />)
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLTextAreaElement>(
          'SQL source for review.inspection',
        ).value,
      ).toBe('SELECT 2'),
    )
    await userEvent.type(
      screen.getByLabelText('SQL source for review.inspection'),
      ' -- keep me',
    )
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    act(() => view.router.history.push('/assets?env=sandbox'))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(view.router.history.location.pathname).toBe('/transform')
    expect(
      screen.getByLabelText<HTMLTextAreaElement>(
        'SQL source for review.inspection',
      ).value,
    ).toBe('SELECT 2 -- keep me')
    confirm.mockReturnValue(true)
    act(() => view.router.history.push('/assets?env=sandbox'))
    await waitFor(() =>
      expect(view.router.history.location.pathname).toBe('/assets'),
    )
  })
  it('preserves edits made during a deferred save and compiles only the current saved revision', async () => {
    const user = userEvent.setup()
    let finish!: (value: typeof draft) => void
    api.save.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    api.compile.mockResolvedValue({
      ...draft,
      status: 'ready',
      diagnostics: [],
      plan_id: 'real-plan',
      compiled_sql: 'SELECT * FROM iceberg.gold.batches',
      target: 'iceberg.review.inspection',
      sql_hash: 'hash',
      desired_version: 'version',
      materialization_available: false,
    })
    setup()
    const input = await screen.findByLabelText(
      'SQL source for review.inspection',
    )
    await user.click(
      screen.getByRole('button', { name: 'Save draft revision' }),
    )
    await user.clear(input)
    await user.type(input, 'SELECT batch_id FROM gold.batches')
    await act(() => finish(draft))
    expect((input as HTMLTextAreaElement).value).toBe(
      'SELECT batch_id FROM gold.batches',
    )
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Compile and plan',
      }).disabled,
    ).toBe(true)
    api.save.mockResolvedValue({
      ...draft,
      sql: 'SELECT batch_id FROM gold.batches',
      revision: 2,
    })
    await user.click(
      screen.getByRole('button', { name: 'Save draft revision' }),
    )
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2))
    expect(api.save.mock.calls[1][0].data.expected_revision).toBe(1)
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>('button', {
          name: 'Compile and plan',
        }).disabled,
      ).toBe(false),
    )
    await user.click(screen.getByRole('button', { name: 'Compile and plan' }))
    expect(api.compile).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        model: model.model,
        draft_id: draft.draft_id,
        revision: 2,
      },
    })
    expect(
      (await screen.findByRole('region', { name: 'Compiled model SQL' }))
        .textContent,
    ).toContain('iceberg.gold.batches')
  })
  it('restores a pinned draft revision and shows rejected source reads without save access', async () => {
    api.read.mockResolvedValue(draft)
    const first = setup({ draftId: draft.draft_id, revision: 1 })
    await screen.findByLabelText('SQL source for review.inspection')
    expect(api.read).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        model: model.model,
        draft_id: draft.draft_id,
        revision: 1,
      },
    })
    first.unmount()
    api.source.mockRejectedValue(new Error('Source access denied'))
    render(
      <TransformEditor
        env="sandbox"
        model={model}
        focus={false}
        onFocus={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Source access denied',
    )
    expect(
      screen.getByRole<HTMLButtonElement>('button', {
        name: 'Save draft revision',
      }).disabled,
    ).toBe(true)
  })
  it('does not navigate after an in-flight save completes after unmount', async () => {
    let finish!: (value: typeof draft) => void
    api.save.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const onSaved = vi.fn()
    const view = setup({ onSaved })
    await screen.findByLabelText('SQL source for review.inspection')
    await userEvent.click(
      screen.getByRole('button', { name: 'Save draft revision' }),
    )
    view.unmount()
    await act(() => finish(draft))
    expect(onSaved).not.toHaveBeenCalled()
  })
})

it('opens a selected immutable revision and compares actual source without discarding edits', async () => {
  const revisions = [2, 1].map((revision) => ({
    ...draft,
    revision,
    path: 'transforms/inspection.sql',
    created_at: '2026-10-22T10:00:00Z',
  }))
  api.read.mockImplementation(({ data }: { data: { revision: number } }) =>
    Promise.resolve({
      ...draft,
      revision: data.revision,
      sql: `SELECT ${data.revision} AS saved_revision`,
    }),
  )
  api.compare.mockResolvedValue({
    env: 'sandbox',
    draft_id: draft.draft_id,
    revision: 1,
    revision_digest: draft.revision_digest,
    source_digest: source.source_digest,
    source_path: source.path,
    base_kind: 'workspace_source',
    baseline_matches_saved_source: false,
    added_lines: 1,
    removed_lines: 1,
    unified_diff: [
      '--- Current workspace source',
      '+++ Saved SQL revision 1',
      '-SELECT old',
      '+SELECT 1 AS saved_revision',
    ],
  })
  const onSaved = vi.fn()
  setup({ draftId: draft.draft_id, revision: 2, onSaved }, revisions)
  const input = await screen.findByLabelText('SQL source for review.inspection')
  const selector = await screen.findByRole<HTMLSelectElement>('combobox', {
    name: 'Saved SQL revision',
  })
  await waitFor(() => expect(selector.options.length).toBe(2))
  await userEvent.selectOptions(selector, `${draft.draft_id}:1`)
  await waitFor(() =>
    expect((input as HTMLTextAreaElement).value).toBe(
      'SELECT 1 AS saved_revision',
    ),
  )
  expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ revision: 1 }))
  await userEvent.click(screen.getByRole('button', { name: 'Compare source' }))
  expect(
    (await screen.findByRole('region', { name: 'SQL source comparison' }))
      .textContent,
  ).toContain('+SELECT 1 AS saved_revision')
  expect(
    screen.getByText(/Workspace source changed after this revision/),
  ).toBeTruthy()
  await userEvent.click(screen.getByRole('button', { name: 'Source' }))
  await userEvent.type(
    screen.getByLabelText('SQL source for review.inspection'),
    ' -- unsaved',
  )
  expect(selector.disabled).toBe(true)
  expect(
    screen.getByText('Save changes before switching revisions.'),
  ).toBeTruthy()
})
