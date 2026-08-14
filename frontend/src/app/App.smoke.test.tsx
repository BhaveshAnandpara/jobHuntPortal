/**
 * Skeleton-step smoke test — proves the app renders, the router mounts,
 * and the QueryClient provider doesn't throw during a render. Feature
 * behavior is NOT covered here (see docs/frontend/testing-strategy.md) —
 * this is infrastructure verification only, per Step 11's scope.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { App } from './App'
import { setCurrentUserId } from '../hooks/identity'

describe('App', () => {
  // `App` mounts a real `BrowserRouter`, which reads/writes the jsdom
  // `window.location` — that location persists across tests within this
  // file (e.g. the redirect-to-/welcome test below leaves the URL at
  // /welcome), so each test must start from a known path itself rather
  // than relying on the previous test's end state.
  beforeEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('renders without an identity and redirects to /welcome', () => {
    render(<App />)

    expect(screen.getByText('Welcome')).toBeInTheDocument()
  })

  it('renders the app shell and the dashboard route once an identity exists', () => {
    setCurrentUserId('user-123')
    render(<App />)

    // The route resolved through <RequireIdentity> + <Layout> to the
    // Dashboard placeholder — proves the full route table (guard → layout
    // → route → page) wires together, not just the unauthenticated path.
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    // Every documented nav destination (routes.md) is reachable from the
    // shell's navigation.
    expect(screen.getAllByRole('link', { name: /Resumes/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /Opportunities/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /Outreach/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /Settings/i }).length).toBeGreaterThan(0)
  })

  it('exposes a mobile navigation toggle that opens the nav menu', async () => {
    setCurrentUserId('user-123')
    render(<App />)

    const toggle = screen.getByRole('button', { name: /open navigation menu/i })
    await userEvent.click(toggle)

    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeInTheDocument()
  })
})
