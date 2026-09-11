/**
 * `<RequireIdentity>` redirect-logic tests — see this repo's
 * `docs/frontend/agent-ownership.md#frontend-shell-agent` testing
 * responsibility list. Verifies the guard redirects to `/login` when no
 * token is persisted, and renders the protected route's `<Outlet />`
 * once one exists — the counterpart to `/login`/`/register` themselves
 * redirecting to `/` on success, which is `frontend-profile-agent`'s
 * `LoginPage`/`RegisterPage` responsibility, not tested here.
 *
 * T3 additionally covers the "session expired / redirected to login" state
 * being explicit rather than silent (spec Section 4, T3): the notice, its
 * suppression on a deliberate sign-out, the `from` route being carried
 * through, and the protected shell never rendering ahead of the redirect.
 *
 * Owner: frontend-shell-agent.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { RequireIdentity, SIGN_IN_REQUIRED_MESSAGE } from './RequireIdentity'
import { consumeIntentionalSignOut, markIntentionalSignOut } from './sessionNotice'
import { IdentityProvider } from '../hooks/IdentityProvider'
import { setToken } from '../hooks/identity'
import { Toaster } from '../components'
import { mintTestToken } from '../../tests/support/jwt'

/**
 * Reports the navigation state the guard handed to `/login`, so the test can
 * assert on it the way a real login page would read it.
 */
function LoginPageStub() {
  const location = useLocation()
  const state = location.state as { from?: string } | null
  return (
    <div>
      <p>Login Page</p>
      <p data-testid="from">{state?.from ?? 'none'}</p>
    </div>
  )
}

function renderAt(initialPath: string) {
  return render(
    <IdentityProvider>
      {/*
        The real app mounts exactly one <Toaster> in app/providers.tsx; it is
        mounted here too so the guard's notice is asserted as the user would
        actually see it, not as a spied-on function call.
      */}
      <Toaster />
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<LoginPageStub />} />
          <Route element={<RequireIdentity />}>
            <Route path="/" element={<div>Protected Dashboard</div>} />
            <Route path="/settings" element={<div>Protected Settings</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </IdentityProvider>,
  )
}

afterEach(() => {
  // The sign-out flag is module-scoped and one-shot; drain it so a test that
  // sets it can never leak into the next one.
  consumeIntentionalSignOut()
})

describe('RequireIdentity', () => {
  it('redirects to /login when no token is persisted', () => {
    renderAt('/')
    expect(screen.getByText('Login Page')).toBeInTheDocument()
    expect(screen.queryByText('Protected Dashboard')).not.toBeInTheDocument()
  })

  it('renders the protected route when a token is persisted', () => {
    setToken(mintTestToken('user-123'))
    renderAt('/')
    expect(screen.getByText('Protected Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument()
  })

  it('explains the redirect instead of silently landing on the login page', async () => {
    renderAt('/settings')

    expect(await screen.findByText(SIGN_IN_REQUIRED_MESSAGE)).toBeInTheDocument()
  })

  it('stays silent when the user deliberately signed out', async () => {
    markIntentionalSignOut()
    renderAt('/settings')

    expect(screen.getByText('Login Page')).toBeInTheDocument()
    // Nothing to explain — the user asked to leave. Give the toast a chance
    // to appear before asserting that it did not.
    await Promise.resolve()
    expect(screen.queryByText(SIGN_IN_REQUIRED_MESSAGE)).not.toBeInTheDocument()
  })

  it('carries the route the user was trying to reach through to /login', () => {
    renderAt('/settings')

    expect(screen.getByTestId('from')).toHaveTextContent('/settings')
  })

  it('never renders the protected route before redirecting (no flash of a broken shell)', () => {
    renderAt('/settings')

    expect(screen.queryByText('Protected Settings')).not.toBeInTheDocument()
    expect(screen.getByText('Login Page')).toBeInTheDocument()
  })
})
