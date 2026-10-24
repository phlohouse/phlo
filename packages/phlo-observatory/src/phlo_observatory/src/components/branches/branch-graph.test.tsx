// @vitest-environment jsdom
/** Verifies branch graph labels and lanes follow the actual comparison target. */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { BranchGraph } from './branch-graph'
import type { ReactNode } from 'react'

vi.mock('@tanstack/react-router', () => ({
  ClientOnly: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@/lib/theme', () => ({ useTheme: () => ({ theme: 'dark' }) }))
vi.mock('@xyflow/react', () => ({
  Position: {},
  Handle: () => null,
  ReactFlow: ({
    nodes,
  }: {
    nodes: Array<{ id: string; data: Record<string, unknown> }>
  }) => <pre data-testid="graph-nodes">{JSON.stringify(nodes)}</pre>,
}))

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const graph = {
  base: 'base123',
  commits: ['work456'],
  behind: 2,
  mergeable: true,
}
const nodes = () => JSON.parse(screen.getByTestId('graph-nodes').textContent)

it('uses a custom target in the drawn labels, comparison captions and accessible evidence', () => {
  render(
    <BranchGraph
      name="sandbox"
      targetName="release_test"
      graph={graph}
      conflicting
    />,
  )
  const label = screen.getByRole('group').getAttribute('aria-label')
  expect(label).toContain('forks from release_test')
  expect(label).toContain('release_test has 2 new commits')
  expect(label).toContain('It can merge into release_test.')
  expect(label).toContain('It conflicts with release_test.')
  expect(
    screen.getByText('release_test: fork point base123, then 2 newer commits'),
  ).toBeTruthy()
  expect(nodes()).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: 'label:main',
        data: { text: 'release_test', lane: 'main' },
      }),
      expect.objectContaining({
        data: expect.objectContaining({ caption: '+2 on release_test' }),
      }),
      expect.objectContaining({
        data: expect.objectContaining({
          tip: 'Merging sandbox into release_test',
        }),
      }),
    ]),
  )
  expect(label).not.toContain('main')
})

it('renders the custom target itself as a single lane and retains main comparisons', () => {
  const empty = { ...graph, commits: [], behind: 0, mergeable: false }
  const { rerender } = render(
    <BranchGraph name="release_test" targetName="release_test" graph={empty} />,
  )
  expect(screen.getByRole('group').getAttribute('aria-label')).toBe(
    'release_test at commit base123.',
  )
  expect(
    nodes().some((node: { id: string }) => node.id === 'label:branch'),
  ).toBe(false)
  rerender(<BranchGraph name="release_test" targetName="main" graph={empty} />)
  expect(screen.getByRole('group').getAttribute('aria-label')).toContain(
    'Branch release_test forks from main',
  )
  expect(nodes()).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        id: 'label:main',
        data: { text: 'main', lane: 'main' },
      }),
      expect.objectContaining({ id: 'label:branch' }),
    ]),
  )
})
