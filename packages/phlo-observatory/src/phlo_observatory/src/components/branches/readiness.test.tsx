// @vitest-environment jsdom
/** Keeps unknown branch checks distinct from passing evidence. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BranchReadiness } from './readiness'

afterEach(cleanup)
it('does not infer green checks before executions exist', () => {
  const onChecks = vi.fn()
  render(<BranchReadiness busy={false} onChecks={onChecks} />)
  expect(screen.getAllByText('· not run')).toHaveLength(3)
  fireEvent.click(screen.getByRole('button', { name: 'Run checks' }))
  expect(onChecks).toHaveBeenCalledOnce()
})
it('shows the actual failed check result and its explanation', () => {
  render(
    <BranchReadiness
      busy={false}
      onChecks={vi.fn()}
      checks={[
        {
          name: 'contracts',
          status: 'failed',
          run_id: 'run-1',
          message: 'Schema contract rejected the source export',
        },
      ]}
    />,
  )
  expect(screen.getByText('· failed')).toBeTruthy()
  expect(
    screen.getByText('Schema contract rejected the source export'),
  ).toBeTruthy()
  expect(screen.getAllByText('· not run')).toHaveLength(2)
})
