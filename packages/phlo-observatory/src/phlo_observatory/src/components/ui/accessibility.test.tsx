// @vitest-environment jsdom
import * as React from 'react'
import { axe } from 'vitest-axe'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from './dialog'
import { Button } from './button'
import { PageSkeleton } from '@/components/phlo/states'

afterEach(cleanup)

function ExampleDialog({ open }: { open: boolean }) {
  return (
    <Dialog open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Launch run</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <label>
            Partition key
            <input aria-label="Partition key" />
          </label>
        </DialogBody>
        <DialogFooter>
          <Button>Launch</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function InteractiveExampleDialog() {
  const [open, setOpen] = React.useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>Open launch dialog</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Launch run</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <label>
            Partition key
            <input aria-label="Partition key" />
          </label>
        </DialogBody>
        <DialogFooter>
          <Button>Launch</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

describe('current accessibility primitives', () => {
  it('has no axe violations in the shell landmarks and open dialog', async () => {
    render(
      <>
        <a href="#main-content">Skip to main content</a>
        <nav aria-label="Primary navigation">Navigation</nav>
        <main id="main-content" tabIndex={-1}>
          Current page
        </main>
        <PageSkeleton />
        <ExampleDialog open />
      </>,
    )

    expect(
      screen.getByRole('status', { name: 'Loading' }).getAttribute('aria-busy'),
    ).toBe('true')
    expect(screen.getByRole('dialog', { name: 'Launch run' })).toBeTruthy()
    const results = await axe(document.body)
    expect(results.violations).toHaveLength(0)
  })

  it('axe detects an unnamed button inside the portalled dialog', async () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unsafe action</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <button />
          </DialogBody>
        </DialogContent>
      </Dialog>,
    )
    const dialog = await screen.findByRole('dialog', { name: 'Unsafe action' })
    const results = await axe(document.body)
    const unnamedButton = results.violations.find(
      (violation) => violation.id === 'button-name',
    )
    expect(
      unnamedButton?.nodes.some((node) =>
        node.target.some(
          (selector) =>
            typeof selector === 'string' &&
            dialog.contains(document.querySelector(selector)),
        ),
      ),
    ).toBe(true)
  })

  it('enters, moves focus with Tab and Shift+Tab, escapes, and restores focus', async () => {
    const user = userEvent.setup()
    render(<InteractiveExampleDialog />)
    const trigger = screen.getByRole('button', { name: 'Open launch dialog' })
    await user.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: 'Launch run' })
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    )
    await user.tab()
    expect(dialog.contains(document.activeElement)).toBe(true)
    await user.tab({ shift: true })
    expect(dialog.contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' })
    await waitFor(() =>
      expect(document.querySelector('[role="dialog"]')).toBeNull(),
    )
    expect(document.activeElement).toBe(trigger)
  })

  it('has a named modal dialog', async () => {
    render(<ExampleDialog open />)
    const dialog = await screen.findByRole('dialog', { name: 'Launch run' })
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeTruthy()
  })
})
