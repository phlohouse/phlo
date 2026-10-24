// @vitest-environment jsdom
/** Keeps schema choices bound to observed branch revisions rather than typed hashes. */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SchemaDecisions } from './schema-decisions'
import {
  getBranchDetail,
  getBranchSchemaDiff,
  getBranchesPage,
} from '@/lib/data/api/branches'
import {
  getSchemaDecisions,
  recordSchemaDecision,
} from '@/lib/data/api/incidents'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="/branches">{children}</a>
  ),
}))
vi.mock('@/components/ui/select', () => ({
  Select: ({
    options,
    onValueChange,
    ...props
  }: {
    options: Array<{ value: string; label: string }>
    onValueChange: (value: string) => void
  }) => (
    <select {...props} onChange={(event) => onValueChange(event.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}))
vi.mock('@/lib/data/api/branches', () => ({
  getBranchDetail: vi.fn(),
  getBranchesPage: vi.fn(),
  getBranchSchemaDiff: vi.fn(),
}))
vi.mock('@/lib/data/api/incidents', () => ({
  getSchemaDecisions: vi.fn(),
  recordSchemaDecision: vi.fn(),
  incidentOperationKey: () => 'replay-key',
}))
afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getBranchesPage).mockResolvedValue({
    env: 'sandbox',
    branches: [
      { name: 'main', hash: 'targethash', protected: true },
      { name: 'sandbox-fix', hash: 'sourcehash', protected: false },
    ],
    tags: [],
  } as never)
  vi.mocked(getBranchDetail).mockResolvedValue({
    diff: {
      source_hash: 'sourcehash',
      target_hash: 'targethash',
      items: [{ key: 'bronze.telemetry', status: 'modified' }],
    },
  } as never)
  vi.mocked(getBranchSchemaDiff).mockResolvedValue({
    env: 'sandbox',
    source: 'sandbox-fix',
    target: 'main',
    source_hash: 'sourcehash',
    target_hash: 'targethash',
    table_key: 'bronze.telemetry',
    columns: [
      {
        name: 'do_sat_pct',
        source: {
          field_id: 2,
          name: 'do_sat_pct',
          type: 'double',
          required: false,
          doc: null,
        },
        target: null,
      },
    ],
  })
  vi.mocked(getSchemaDecisions).mockResolvedValue({ items: [] } as never)
  vi.mocked(recordSchemaDecision).mockResolvedValue({} as never)
})

describe('observed schema choices', () => {
  it('loads scoped refs, real table differences and pinned fields before allowing a decision', async () => {
    render(
      <SchemaDecisions env="sandbox" incidentId="incident-1" kind="merge" />,
    )
    await waitFor(() =>
      expect(screen.getByText('do_sat_pct double')).toBeTruthy(),
    )
    expect(getBranchSchemaDiff).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        branch: 'sandbox-fix',
        target: 'main',
        tableKey: 'bronze.telemetry',
        sourceHash: 'sourcehash',
        targetHash: 'targethash',
      },
    })
    expect(
      screen.queryByRole('textbox', { name: 'Observed source hash' }),
    ).toBeNull()
    const submit = screen.getByRole<HTMLButtonElement>('button', {
      name: 'Record schema decision',
    })
    expect(submit.disabled).toBe(true)
    fireEvent.click(screen.getAllByRole('radio')[1])
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Note for the audit log' }),
      { target: { value: 'Use the observed historian field' } },
    )
    expect(submit.disabled).toBe(false)
    fireEvent.click(submit)
    await waitFor(() => expect(recordSchemaDecision).toHaveBeenCalledTimes(1))
    expect(recordSchemaDecision).toHaveBeenCalledWith({
      data: {
        env: 'sandbox',
        id: 'incident-1',
        idempotencyKey: 'replay-key',
        decision: {
          source_ref: 'sandbox-fix',
          target_ref: 'main',
          source_hash: 'sourcehash',
          target_hash: 'targethash',
          table_key: 'bronze.telemetry',
          columns: { do_sat_pct: 'source' },
          justification: 'Use the observed historian field',
        },
      },
    })
  })
  it.each([
    'current',
    'other source hash',
    'other target hash',
    'other table',
    'other branch',
  ])(
    'shows immutable choices only for an exact pinned decision: %s',
    async (variant) => {
      vi.mocked(getSchemaDecisions).mockResolvedValue({
        env: 'sandbox',
        incident_id: 'incident-1',
        items: [
          {
            id: 'decision-1',
            env: 'sandbox',
            incident_id: 'incident-1',
            source_ref:
              variant === 'other branch' ? 'another-branch' : 'sandbox-fix',
            target_ref: 'main',
            source_hash:
              variant === 'other source hash' ? 'older-source' : 'sourcehash',
            target_hash:
              variant === 'other target hash' ? 'older-target' : 'targethash',
            table_key:
              variant === 'other table' ? 'bronze.other' : 'bronze.telemetry',
            columns: { do_sat_pct: 'source' },
            justification: 'Reviewed actual source field',
            actor: 'dev_user',
            created_at: '2026-10-22T19:19:36Z',
          },
        ],
      })
      render(
        <SchemaDecisions env="sandbox" incidentId="incident-1" kind="schema" />,
      )
      if (variant === 'current') {
        await waitFor(() =>
          expect(
            screen.getByRole('article', {
              name: 'Recorded decision for current revisions',
            }),
          ).toBeTruthy(),
        )
        expect(screen.queryByRole('radio')).toBeNull()
        expect(
          screen.queryByRole('button', { name: 'Record schema decision' }),
        ).toBeNull()
        expect(screen.queryByText('Decision needed')).toBeNull()
        expect(screen.getByText('Recorded for these revisions')).toBeTruthy()
      } else {
        await waitFor(() =>
          expect(screen.getAllByRole('radio')).toHaveLength(2),
        )
        expect(
          screen.queryByRole('article', {
            name: 'Recorded decision for current revisions',
          }),
        ).toBeNull()
        expect(
          screen.getByRole<HTMLButtonElement>('button', {
            name: 'Record schema decision',
          }).disabled,
        ).toBe(true)
      }
      expect(screen.getByText('Recorded decisions · 1')).toBeTruthy()
      expect(recordSchemaDecision).not.toHaveBeenCalled()
    },
  )
  it('keeps stale provider errors visible and does not offer guessed fields', async () => {
    vi.mocked(getBranchSchemaDiff).mockRejectedValue(
      new Error(
        'Provider table metadata no longer matches the pinned branch revision.',
      ),
    )
    render(
      <SchemaDecisions env="sandbox" incidentId="incident-1" kind="schema" />,
    )
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain(
        'no longer matches',
      ),
    )
    expect(screen.queryByRole('radio')).toBeNull()
    expect(recordSchemaDecision).not.toHaveBeenCalled()
  })
  it('does not load schema data for unrelated incident kinds', () => {
    render(
      <SchemaDecisions
        env="sandbox"
        incidentId="incident-1"
        kind="freshness"
      />,
    )
    expect(getBranchesPage).not.toHaveBeenCalled()
  })
})
