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
import { describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Bomb(): never {
  throw new Error('boom')
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
    // React logs the caught error to the console (and jsdom logs the
    // "uncaught" error event too) — expected noise for this test, not a
    // real failure; silence both so the test output stays readable.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Something went wrong.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()

    consoleError.mockRestore()
  })
})
