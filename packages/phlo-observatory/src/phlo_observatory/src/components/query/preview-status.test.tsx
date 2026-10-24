// @vitest-environment jsdom
/** Covers overlapping observations and honest connection-only presentation. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { QueryPreviewStatus } from './preview-status'

const invalidate = vi.fn().mockResolvedValue(undefined)
vi.mock('@tanstack/react-router', () => ({ useRouter: () => ({ invalidate }) }))
afterEach(() => {
  cleanup()
  invalidate.mockClear()
})
const connection = {
  state: 'configured' as const,
  reason_code: 'backend_reachable',
  message: 'Authenticated preview probe succeeded.',
  remediation: 'Deployment enforcement remains unverified.',
}

it('summarizes overlapping legacy checks once without upgrading to ready', () => {
  render(
    <QueryPreviewStatus
      checks={[
        { ...connection, reason_code: 'enforcement_unverified' },
        connection,
        connection,
      ]}
    />,
  )
  expect(screen.getAllByRole('status')).toHaveLength(1)
  expect(screen.getByText('Query preview connected')).toBeTruthy()
  expect(screen.getAllByText(/Passed: authenticated connection/)).toHaveLength(
    1,
  )
  expect(
    screen.getByText(/Deployment safeguards are not verified/),
  ).toBeTruthy()
  expect(
    screen.getByText(/local development does not disable them/),
  ).toBeTruthy()
  expect(screen.queryByText('Query preview ready')).toBeNull()
  expect(screen.queryByText(/backend_reachable/)).toBeNull()
  expect(screen.getByRole('status').className).not.toContain('bg-warn-soft')
})

it('gives a failed dependency priority over a successful connection', () => {
  const failed = {
    ...connection,
    state: 'unavailable' as const,
    reason_code: 'catalog_unavailable',
    message: 'The catalog could not be read.',
    remediation: 'Check catalog credentials.',
  }
  render(<QueryPreviewStatus checks={[connection, failed, failed]} />)
  expect(screen.getByText('Query preview unavailable')).toBeTruthy()
  expect(screen.getAllByText(failed.message)).toHaveLength(1)
  expect(screen.getByText(failed.remediation)).toBeTruthy()
  expect(screen.getByRole('status').className).toContain('bg-warn-soft')
})

it('keeps a disabled preview explicit even alongside an old connection result', () => {
  render(
    <QueryPreviewStatus
      checks={[
        connection,
        {
          ...connection,
          state: 'disabled',
          message: 'Disabled by configuration.',
        },
      ]}
    />,
  )
  expect(screen.getByText('Query preview disabled')).toBeTruthy()
  expect(screen.queryByText('Query preview connected')).toBeNull()
})

it('requires all observations to be ready and can refresh the observation', () => {
  const { rerender } = render(
    <QueryPreviewStatus
      checks={[{ ...connection, state: 'ready' }, connection]}
    />,
  )
  expect(screen.queryByText('Query preview ready')).toBeNull()
  rerender(<QueryPreviewStatus checks={[{ ...connection, state: 'ready' }]} />)
  expect(screen.getByText('Query preview ready')).toBeTruthy()
  fireEvent.click(
    screen.getByRole('button', { name: 'Check again', hidden: true }),
  )
  expect(invalidate).toHaveBeenCalledOnce()
})

it('does not infer connectivity from configuration or absent observations', () => {
  const { rerender } = render(
    <QueryPreviewStatus
      checks={[{ ...connection, reason_code: 'configured' }]}
    />,
  )
  expect(screen.getByText('Query preview configured')).toBeTruthy()
  expect(screen.getByText(/connection has not been verified/)).toBeTruthy()
  rerender(<QueryPreviewStatus checks={[]} />)
  expect(screen.getByText('Query preview unavailable')).toBeTruthy()
})
