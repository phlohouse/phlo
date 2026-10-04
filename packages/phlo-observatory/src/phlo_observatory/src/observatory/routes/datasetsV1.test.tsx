// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DatasetProfile } from '@/routes/datasets.$datasetId'
import { Datasets } from '@/routes/datasets'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  Outlet: () => null,
  createFileRoute: () => () => ({
    useParams: () => ({ datasetId: 'ignored' }),
  }),
  useMatches: () => [],
}))

afterEach(cleanup)

describe('replacement Dataset routes', () => {
  it('does not render Dagster assets as Dataset inventory', () => {
    render(<Datasets />)
    expect(screen.getByText('No matching v1 Dataset contract')).toBeTruthy()
    expect(screen.getByText(/not shown here as substitutes/)).toBeTruthy()
  })

  it('keeps a requested Dataset identifier navigable without inventing detail', () => {
    render(<DatasetProfile datasetId="governed.orders" />)
    expect(
      screen.getByRole('heading', { name: 'governed.orders' }),
    ).toBeTruthy()
    expect(screen.getByText(/does not use Dagster asset data/)).toBeTruthy()
  })
})
