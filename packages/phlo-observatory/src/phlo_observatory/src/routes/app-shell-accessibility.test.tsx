// @vitest-environment jsdom
import * as React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { Route } from './_app'
import { ThemeProvider } from '@/lib/theme'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: { component?: React.ComponentType }) => ({
    options,
    useLoaderData: () => ({
      overview: {
        env: 'prod',
        incident_counts: { open: 0, acknowledged: 0 },
      },
      services: [],
      me: {
        principal_type: 'user',
        subject: 'shell-test',
        email: null,
        roles: ['admin'],
        permissions: {},
      },
      incidents: [],
    }),
  }),
  Link: ({ to, children }: { to?: string; children: React.ReactNode }) => (
    <a href={to ?? '/'}>{children}</a>
  ),
  Outlet: () => <h1>Current route content</h1>,
  useNavigate: () => () => undefined,
  useRouterState: ({
    select,
  }: {
    select: (state: { matches: Array<never> }) => unknown
  }) => select({ matches: [] }),
}))

afterEach(cleanup)

describe('the AppLayout shell', () => {
  it('exposes the real navigation landmarks and focuses main from its skip link', async () => {
    const user = userEvent.setup()
    const Shell = Route.options.component
    if (!Shell) throw new Error('AppLayout is not registered on the route.')
    render(
      <ThemeProvider>
        <Shell />
      </ThemeProvider>,
    )

    const skipLink = screen.getByRole('link', { name: 'Skip to main content' })
    expect(
      screen.getByRole('navigation', { name: 'Primary navigation' }),
    ).toBeTruthy()
    expect(
      screen.getByRole('navigation', { name: 'Mobile navigation' }),
    ).toBeTruthy()
    for (const name of ['Primary navigation', 'Mobile navigation']) {
      const navigation = within(screen.getByRole('navigation', { name }))
      expect(
        navigation.getByRole('link', { name: 'Jobs' }).getAttribute('href'),
      ).toBe('/pipelines')
      expect(navigation.queryByRole('link', { name: 'Pipelines' })).toBeNull()
    }
    const main = screen.getByRole('main')
    expect(main.id).toBe('main-content')

    await user.tab()
    expect(document.activeElement).toBe(skipLink)
    await user.keyboard('{Enter}')
    expect(window.location.hash).toBe('#main-content')
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
  })
})
