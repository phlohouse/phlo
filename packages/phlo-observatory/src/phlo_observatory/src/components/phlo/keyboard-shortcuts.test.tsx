// @vitest-environment jsdom
/** Checks that navigation shortcuts cannot intercept normal typing. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KeyboardShortcuts } from './keyboard-shortcuts'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
afterEach(() => {
  cleanup()
  navigate.mockReset()
})

describe('keyboard shortcut navigation', () => {
  it('uses a two-key sequence and retains the selected environment', () => {
    render(<KeyboardShortcuts env="sandbox" />)
    fireEvent.keyDown(window, { key: 'g' })
    fireEvent.keyDown(window, { key: 'q' })
    expect(navigate).toHaveBeenCalledWith({
      to: '/query',
      search: { env: 'sandbox' },
    })
    navigate.mockClear()
    fireEvent.keyDown(window, { key: 'a' })
    expect(navigate).not.toHaveBeenCalled()
  })
  it('does not navigate while typing in form fields or SQL editors', () => {
    render(
      <>
        <KeyboardShortcuts env="sandbox" />
        <input aria-label="Form field" />
        <div role="textbox" contentEditable aria-label="SQL editor" />
      </>,
    )
    for (const name of ['Form field', 'SQL editor']) {
      const input = screen.getByRole('textbox', { name })
      fireEvent.keyDown(input, { key: 'g' })
      fireEvent.keyDown(input, { key: 'q' })
    }
    expect(navigate).not.toHaveBeenCalled()
  })
  it('opens and searches help with the question-mark shortcut', async () => {
    const user = userEvent.setup()
    render(<KeyboardShortcuts env="sandbox" />)
    fireEvent.keyDown(window, { key: '?' })
    expect(
      screen.getByRole('dialog', { name: 'Keyboard shortcuts' }),
    ).toBeTruthy()
    await user.type(
      screen.getByRole('textbox', { name: 'Search shortcuts' }),
      'pipelines',
    )
    expect(screen.getByText('Go to Pipelines')).toBeTruthy()
    expect(screen.queryByText('Go to Assets')).toBeNull()
    fireEvent.keyDown(window, { key: 'g' })
    fireEvent.keyDown(window, { key: 'q' })
    expect(navigate).not.toHaveBeenCalled()
  })
})
