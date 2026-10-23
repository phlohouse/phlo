// @vitest-environment jsdom
/** Tests actual inbox filters and actor-owned receipt requests without invented activity. */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationsButton } from './inbox'
import {
  getNotifications,
  markNotificationsRead,
} from '@/lib/data/api/notifications'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    onClick,
  }: {
    children: React.ReactNode
    onClick: () => void
  }) => (
    <a
      href="/incidents/incident-1"
      onClick={(event) => {
        event.preventDefault()
        onClick()
      }}
    >
      {children}
    </a>
  ),
}))
vi.mock('@/lib/data/api/notifications', () => ({
  getNotifications: vi.fn(),
  markNotificationsRead: vi.fn(),
}))
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean
    onOpenChange: (value: boolean) => void
    children: React.ReactNode
  }) =>
    open ? (
      <div role="dialog">
        <button onClick={() => onOpenChange(false)}>Close</button>
        {children}
      </div>
    ) : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: React.ReactNode }) => (
    <h2>{children}</h2>
  ),
}))
afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getNotifications).mockImplementation(({ data }) =>
    Promise.resolve({
      env: data.env,
      view: data.view ?? 'for_you',
      unread: 1,
      truncated: false,
      items: [
        {
          id: 'event-1',
          incident_id: 'incident-1',
          title: 'Audit failed on actual results',
          asset_id: 'silver/qc_results',
          actor: 'actual-sensor',
          kind: 'signal',
          occurred_at: '2026-10-22T12:00:00Z',
          read: false,
        },
      ],
    }),
  )
  vi.mocked(markNotificationsRead).mockResolvedValue({ event_ids: ['event-1'] })
})

describe('notification inbox', () => {
  it('pages older activity and resets continuation when filtering unread events', async () => {
    vi.mocked(getNotifications).mockImplementation(({ data }) =>
      Promise.resolve({
        env: data.env,
        view: data.view ?? 'for_you',
        unread: 1,
        truncated: !data.after,
        next_cursor: data.after ? null : 'opaque+/cursor=',
        items: [
          {
            id: data.after ? 'event-2' : 'event-1',
            incident_id: 'incident-1',
            title: data.after
              ? 'Older incident activity'
              : 'Newest incident activity',
            asset_id: 'silver/qc_results',
            actor: 'actual-sensor',
            kind: 'comment',
            occurred_at: '2026-10-22T12:00:00Z',
            read: false,
          },
        ],
      }),
    )
    render(<NotificationsButton env="sandbox" />)
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    await screen.findByText(/Newest incident activity/)
    fireEvent.click(screen.getByRole('button', { name: 'Older activity' }))
    await screen.findByText(/Older incident activity/)
    expect(getNotifications).toHaveBeenLastCalledWith({
      data: { env: 'sandbox', view: 'for_you', after: 'opaque+/cursor=' },
    })
    fireEvent.click(screen.getByLabelText('Unread only'))
    await screen.findByText(/Newest incident activity/)
    expect(getNotifications).toHaveBeenLastCalledWith({
      data: { env: 'sandbox', view: 'for_you', unread_only: true },
    })
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Newer activity' })
        .disabled,
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
    await screen.findByText('0 unread in this list')
    expect(screen.queryByText(/Newest incident activity/)).toBeNull()
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Older activity' })
        .disabled,
    ).toBe(false)
  })
  it('loads only after opening, switches scoped filters and marks the listed event', async () => {
    render(<NotificationsButton env="sandbox" />)
    expect(getNotifications).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    await waitFor(() =>
      expect(screen.getByText('actual-sensor', { exact: false })).toBeTruthy(),
    )
    expect(getNotifications).toHaveBeenCalledWith({
      data: { env: 'sandbox', view: 'for_you' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Watching' }))
    await waitFor(() =>
      expect(getNotifications).toHaveBeenLastCalledWith({
        data: { env: 'sandbox', view: 'watching' },
      }),
    )
    await waitFor(() =>
      expect(
        screen
          .getByRole('button', { name: 'Mark all read' })
          .hasAttribute('disabled'),
      ).toBe(false),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }))
    await waitFor(() =>
      expect(markNotificationsRead).toHaveBeenCalledWith({
        data: { env: 'sandbox', eventIds: ['event-1'] },
      }),
    )
    await waitFor(() =>
      expect(screen.getByText('0 unread in this list')).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('link', { name: 'Open incident' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('shows an honest exact-owner empty state instead of creating For you messages', async () => {
    vi.mocked(getNotifications).mockResolvedValue({
      env: 'sandbox',
      view: 'for_you',
      items: [],
      unread: 0,
      truncated: false,
    })
    render(<NotificationsButton env="sandbox" />)
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    await waitFor(() =>
      expect(screen.getByText('No notifications assigned to you')).toBeTruthy(),
    )
    expect(screen.getByText(/owner exactly matches/)).toBeTruthy()
    expect(markNotificationsRead).not.toHaveBeenCalled()
  })
  it('does not show successful read state when persistence fails', async () => {
    vi.mocked(markNotificationsRead).mockRejectedValue(
      new Error('Read receipt unavailable'),
    )
    render(<NotificationsButton env="sandbox" />)
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Mark read' })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Mark read' }))
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain(
        'Read receipt unavailable',
      ),
    )
    expect(screen.getByText('1 unread in this list')).toBeTruthy()
  })
})
