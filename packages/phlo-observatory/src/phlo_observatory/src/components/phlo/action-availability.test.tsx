// @vitest-environment jsdom
/** Disabled actions explain missing preflight and resource checks stay scoped. */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ActionAvailabilityProvider } from './action-availability'
import { Button } from '@/components/ui/button'
import { getActionAvailability } from '@/lib/data/api/action-availability'

vi.mock('@/lib/data/api/action-availability', () => ({
  getActionAvailability: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('fails closed with an accessible reason and checks the selected run, not env-only permission', async () => {
  vi.mocked(getActionAvailability).mockResolvedValue({
    env: 'research',
    actions: { v1_run_retry: { allowed: true, reason: null } },
  })
  render(
    <ActionAvailabilityProvider
      env="research"
      initial={{
        env: 'research',
        actions: {
          v1_data_product_create: {
            allowed: false,
            reason: 'Dataset write permission required.',
          },
        },
      }}
    >
      <Button operation="v1_data_product_create">New product</Button>
      <Button operation="v1_query_submit">Run query</Button>
      <Button operation="v1_run_retry" actionContext={{ run_id: 'run-42' }}>
        Retry run
      </Button>
    </ActionAvailabilityProvider>,
  )
  const denied = screen.getByRole<HTMLButtonElement>('button', {
    name: 'New product',
  })
  expect(denied.disabled).toBe(true)
  expect(
    document.getElementById(denied.getAttribute('aria-describedby')!)
      ?.textContent,
  ).toBe('Dataset write permission required.')
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Run query' })
      .disabled,
  ).toBe(true)
  await waitFor(() =>
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Retry run' })
        .disabled,
    ).toBe(false),
  )
  expect(getActionAvailability).toHaveBeenCalledWith({
    data: { env: 'research', run_id: 'run-42' },
  })
})
