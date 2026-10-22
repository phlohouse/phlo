// @vitest-environment jsdom
/** Source creation must require review and retain truthful activation boundaries. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RestApiForm } from './rest-api-form'
import type * as SourcesModule from '@/lib/data/api/sources'
import { createSourceWorkflow } from '@/lib/data/api/sources'

vi.mock('@/lib/data/api/sources', async (original) => ({
  ...(await original<typeof SourcesModule>()),
  createSourceWorkflow: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
function review() {
  for (const [label, value] of [
    ['Domain', 'lab'],
    ['Table', 'samples'],
    ['Unique key', 'sample_id'],
    ['API base URL', 'https://example.org/api'],
    [
      'Columns as name:type, separated by commas',
      'sample_id:str, result:float?',
    ],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByRole('button', { name: 'Review workflow files' }))
}
describe('REST API authoring review', () => {
  it('does not write files before a separate reviewed submission and shows actual created files', async () => {
    vi.mocked(createSourceWorkflow).mockResolvedValue({
      workflow_type: 'ingestion',
      provider: 'dlt',
      domain: 'lab',
      table: 'samples',
      files: ['workflows/ingestion/lab/samples.py'],
      next_steps: ['Review generated source'],
      validations: [
        { file: 'workflows/ingestion/lab/samples.py', valid: true, errors: [] },
      ],
    })
    render(<RestApiForm onClose={() => {}} />)
    review()
    expect(createSourceWorkflow).not.toHaveBeenCalled()
    expect(screen.getByText('Review project changes')).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'Create workflow files' }),
    )
    expect(await screen.findByText('Workflow files created')).toBeTruthy()
    expect(screen.getByText(/These files are not active yet/)).toBeTruthy()
    expect(createSourceWorkflow).toHaveBeenCalledWith({
      data: {
        domain: 'lab',
        table: 'samples',
        unique_key: 'sample_id',
        api_base_url: 'https://example.org/api',
        fields: ['sample_id:str', 'result:float?'],
      },
    })
  })
  it('shows authorization failures without claiming files were created', async () => {
    vi.mocked(createSourceWorkflow).mockRejectedValue(
      new Error('Your account does not have permission for this action.'),
    )
    render(<RestApiForm onClose={() => {}} />)
    review()
    await userEvent.click(
      screen.getByRole('button', { name: 'Create workflow files' }),
    )
    expect((await screen.findByRole('alert')).textContent).toContain(
      'does not have permission',
    )
    expect(screen.queryByText('Workflow files created')).toBeNull()
  })
})
