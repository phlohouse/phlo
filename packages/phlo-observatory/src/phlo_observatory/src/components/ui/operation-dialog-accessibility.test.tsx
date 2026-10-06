// @vitest-environment jsdom
import * as React from 'react'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'
import { MaterializeDialog } from '@/components/assets/materialize-dialog'
import { MergeDialog } from '@/components/branches/merge-dialog'

const branchApi = vi.hoisted(() => ({
  getBranchesPage: vi.fn(),
}))

vi.mock('@/lib/data/api/branches', async (importOriginal) => ({
  ...(await importOriginal()),
  getBranchesPage: branchApi.getBranchesPage,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const mainRef = {
  env: 'prod' as const,
  name: 'main',
  type: 'BRANCH' as const,
  hash: '1234567890abcdef',
  protected: true,
}

describe('current materialisation and signature dialogs', () => {
  it('renders the Materialize dialog with named controls and no axe violations', async () => {
    const user = userEvent.setup()
    branchApi.getBranchesPage.mockResolvedValue({
      env: 'prod',
      branches: [mainRef],
      tags: [],
    })
    function MaterializeHarness() {
      const [open, setOpen] = React.useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>Open materialize dialog</button>
          <MaterializeDialog
            open={open}
            onClose={() => setOpen(false)}
            assetId="analytics.daily_revenue"
            env="prod"
            jobs={[]}
          />
        </>
      )
    }

    render(<MaterializeHarness />)
    const opener = screen.getByRole('button', {
      name: 'Open materialize dialog',
    })
    await user.click(opener)
    const dialog = await screen.findByRole('dialog', { name: 'Materialize' })
    await waitFor(() => expect(branchApi.getBranchesPage).toHaveBeenCalled())
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    )
    expect(
      within(dialog).getByRole('radio', { name: /Next increment only/ }),
    ).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: 'Run now' })).toBeTruthy()
    await user.tab()
    expect(dialog.contains(document.activeElement)).toBe(true)
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(document.querySelector('[role="dialog"]')).toBeNull(),
    )
    expect(document.activeElement).toBe(opener)
  })

  it('renders the signature dialog with named controls and modal focus behavior', async () => {
    const user = userEvent.setup()
    function SignatureHarness() {
      const [open, setOpen] = React.useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>Open signature dialog</button>
          <MergeDialog
            open={open}
            branch={{ ...mainRef, name: 'prod-feature', protected: false }}
            target={mainRef}
            busy={false}
            onClose={() => setOpen(false)}
            onChecks={() => undefined}
            onTrial={() => undefined}
            onMerge={() => undefined}
            onMessageChange={() => undefined}
          />
        </>
      )
    }

    render(<SignatureHarness />)
    const opener = screen.getByRole('button', { name: 'Open signature dialog' })
    await user.click(opener)

    const dialog = await screen.findByRole('dialog', {
      name: /Merge prod-feature into main/,
    })
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    )
    expect(
      within(dialog).getByRole('textbox', {
        name: 'Merge message and signature justification',
      }),
    ).toBeTruthy()
    expect(
      within(dialog).getByRole('group', { name: 'Electronic signature' }),
    ).toBeTruthy()
    expect(
      within(dialog).getByRole('button', { name: 'Sign and merge' }),
    ).toBeTruthy()
    await user.tab()
    expect(dialog.contains(document.activeElement)).toBe(true)
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(document.querySelector('[role="dialog"]')).toBeNull(),
    )
    expect(document.activeElement).toBe(opener)
  })
})
