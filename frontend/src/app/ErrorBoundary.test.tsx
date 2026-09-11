/**
 * Verifies `<ErrorBoundary>` actually catches a render-time exception from
 * a child and renders the fallback instead of unmounting the whole shell —
 * see this file's sibling `ErrorBoundary.tsx` header comment and the Wave 1
 * brief's "hardened, verify it actually catches and displays gracefully"
 * requirement.
 *
 * Owner: frontend-shell-agent.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Bomb(): never {
  throw new Error('boom: a very raw internal message')
}

/**
 * Renders the fallback with React's caught-error console noise silenced, and
 * hands back the restore function. Every test that trips the boundary needs
 * this — React logs the caught error, and jsdom logs the "uncaught" error
 * event on top of it; both are expected here, not real failures.
 */
function renderCrashed() {
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  render(
    <ErrorBoundary>
      <Bomb />
    </ErrorBoundary>,
  )
  return consoleError
}

describe('ErrorBoundary', () => {
  it('renders children normally when nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>All good</p>
      </ErrorBoundary>,
    )

    expect(screen.getByText('All good')).toBeInTheDocument()
  })

  it('catches a render-time exception and shows the fallback instead of crashing', () => {
    const consoleError = renderCrashed()

    expect(screen.getByText('Something went wrong.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()

    consoleError.mockRestore()
  })

  // T3: the fallback has to be a way *out*, not a dead end — see
  // docs/frontend/frontend-revamp-spec.md T3's "error boundary fallback needs
  // a real recovery action".
  it('announces the failure as an alert so it is not a silently blank screen', () => {
    const consoleError = renderCrashed()

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong.')
    // Explains what happened and what to do, rather than four bare words.
    expect(alert).toHaveTextContent(/reloading/i)

    consoleError.mockRestore()
  })

  it('offers a working reload as the primary recovery action', async () => {
    const consoleError = renderCrashed()

    // jsdom does not implement navigation, so `location.reload` is stubbed
    // rather than called for real; the assertion is that the button is wired
    // to it at all, which is the behavior that would otherwise regress into
    // a decorative button.
    const originalLocation = window.location
    const reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...originalLocation, reload },
    })

    try {
      await userEvent.click(screen.getByRole('button', { name: 'Reload' }))
      expect(reload).toHaveBeenCalledTimes(1)
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        writable: true,
        value: originalLocation,
      })
      consoleError.mockRestore()
    }
  })

  it('offers a second escape hatch back to the dashboard for a page that keeps failing', () => {
    const consoleError = renderCrashed()

    expect(screen.getByRole('link', { name: 'Back to dashboard' })).toHaveAttribute('href', '/')

    consoleError.mockRestore()
  })

  it('never surfaces the raw exception message to the user', () => {
    const consoleError = renderCrashed()

    // docs/frontend/error-handling.md's "what never happens": internal error
    // text is a console concern, not a UI one.
    expect(screen.queryByText(/a very raw internal message/)).not.toBeInTheDocument()

    consoleError.mockRestore()
  })
})
