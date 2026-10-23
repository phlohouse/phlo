// @vitest-environment jsdom
/** Disabled actions explain missing preflight and resource checks stay scoped. */
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ActionAvailabilityProvider } from './action-availability'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { getActionAvailability } from '@/lib/data/api/action-availability'
import { ActionGroup } from '@/components/ui/action-group'

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

it('groups duplicate denials without losing distinct reasons or enabling actions', async () => {
  const user = userEvent.setup()
  const denied = {
    allowed: false,
    reason: 'Availability could not be verified.',
  }
  const actions = {
    save: denied,
    explain: denied,
    run: { allowed: false, reason: 'Query preview is disabled.' },
  }
  const view = (allowed: boolean) => (
    <ActionAvailabilityProvider
      env="research"
      initial={{
        env: 'research',
        actions: allowed
          ? Object.fromEntries(
              Object.keys(actions).map((key) => [
                key,
                { allowed: true, reason: null },
              ]),
            )
          : actions,
      }}
    >
      <ActionGroup>
        <Button operation="save">Save</Button>
        <Button operation="explain">Explain</Button>
        <Button operation="run">Run</Button>
      </ActionGroup>
    </ActionAvailabilityProvider>
  )
  const rendered = render(view(false))
  const disclosure = screen.getByText('3 actions unavailable · View reasons')
  const details = disclosure.closest('details')!
  expect(details.open).toBe(false)
  await user.click(disclosure)
  expect(details.open).toBe(true)
  expect(within(details).getAllByRole('listitem')).toHaveLength(2)
  expect(details.textContent).toContain('Save, Explain:')
  expect(details.textContent).toContain('Query preview is disabled.')
  for (const name of ['Save', 'Explain', 'Run']) {
    const button = screen.getByRole<HTMLButtonElement>('button', { name })
    expect(button.disabled).toBe(true)
    expect(
      document.getElementById(button.getAttribute('aria-describedby')!),
    ).not.toBeNull()
  }
  rendered.rerender(view(true))
  await waitFor(() => expect(screen.queryByText(/View reasons/)).toBeNull())
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'Run' }).disabled,
  ).toBe(false)
})
