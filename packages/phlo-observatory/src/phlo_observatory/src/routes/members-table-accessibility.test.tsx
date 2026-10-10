// @vitest-environment jsdom
/** Verifies the members and service-account tables keep header and cell semantics in populated and empty states. */
import * as React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { Route } from './_app/settings/members'
import type {
  AdminInvitation,
  AdminMember,
  AdminServiceAccount,
} from '@/lib/data/api/admin'

const state: {
  members: Array<AdminMember>
  invitations: Array<AdminInvitation>
  serviceAccounts: Array<AdminServiceAccount>
} = { members: [], invitations: [], serviceAccounts: [] }

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: { component?: React.ComponentType }) => ({
    options,
    useLoaderData: () => state,
  }),
  Link: ({ to, children }: { to?: string; children: React.ReactNode }) => (
    <a href={to ?? '/'}>{children}</a>
  ),
  useRouter: () => ({ invalidate: () => Promise.resolve() }),
  useRouterState: ({
    select,
  }: {
    select: (s: {
      location: { pathname: string; hash: string; search: { env?: string } }
    }) => unknown
  }) =>
    select({
      location: { pathname: '/settings/members', hash: '', search: {} },
    }),
}))

afterEach(cleanup)

function renderPage() {
  const MembersPage = Route.options.component
  if (!MembersPage)
    throw new Error('MembersPage is not registered on the route.')
  return render(
    <main>
      <MembersPage />
    </main>,
  )
}

function structure(table: HTMLElement) {
  const [header, ...rows] = within(table).getAllByRole('row')
  return {
    header,
    headers: within(header)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent),
    cells: rows.map((row) => within(row).getAllByRole('cell').length),
  }
}

describe('the members tables', () => {
  it('give every populated row a cell per column header', async () => {
    state.members = [
      {
        subject: 'user-1',
        email: 'ada@example.com',
        principal_type: 'user',
        roles: ['admin'],
        active: true,
        version: 1,
        created_at: '2026-10-01T00:00:00Z',
        updated_at: '2026-10-02T00:00:00Z',
      },
    ]
    state.invitations = [
      {
        invitation_id: 'inv-1',
        email: 'grace@example.com',
        roles: ['viewer'],
        status: 'pending',
        invited_by: 'user-1',
        expires_at: '2026-10-20T00:00:00Z',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
    state.serviceAccounts = [
      {
        subject: 'svc-1',
        name: 'loader',
        roles: ['service'],
        active: true,
        version: 1,
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
    renderPage()

    const people = structure(screen.getByRole('table', { name: 'People' }))
    expect(people.headers).toEqual([
      'Identity',
      'Type',
      'Roles',
      'Status',
      'Updated',
    ])
    expect(people.cells).toEqual([5, 5])
    const services = structure(
      screen.getByRole('table', { name: 'Service accounts' }),
    )
    expect(services.headers).toEqual(['Account', 'Roles', 'Status', 'Actions'])
    expect(services.cells).toEqual([4])
    // Narrow layouts hide headers visually, never from assistive technology.
    for (const row of [people.header, services.header])
      expect(row.className.split(' ')).not.toContain('hidden')

    expect(await axe(document.body)).toHaveProperty('violations', [])
  })

  it('keep their headers and a message cell when empty', async () => {
    state.members = []
    state.invitations = []
    state.serviceAccounts = []
    renderPage()

    const people = screen.getByRole('table', { name: 'People' })
    expect(structure(people).headers).toHaveLength(5)
    expect(structure(people).cells).toEqual([1])
    expect(within(people).getByRole('cell').textContent).toBe(
      'No members or invitations.',
    )
    const services = screen.getByRole('table', { name: 'Service accounts' })
    expect(structure(services).headers).toHaveLength(4)
    expect(within(services).getByRole('cell').textContent).toBe(
      'No service accounts.',
    )
    expect(await axe(document.body)).toHaveProperty('violations', [])
  })
})
