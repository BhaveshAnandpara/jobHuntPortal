/**
 * `<RequireIdentity>` redirect-logic tests — see this repo's
 * `docs/frontend/agent-ownership.md#frontend-shell-agent` testing
 * responsibility list. Verifies the guard redirects to `/login` when no
 * token is persisted, and renders the protected route's `<Outlet />`
 * once one exists — the counterpart to `/login`/`/register` themselves
 * redirecting to `/` on success, which is `frontend-profile-agent`'s
 * `LoginPage`/`RegisterPage` responsibility, not tested here.
 *
 * Owner: frontend-shell-agent.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { RequireIdentity } from './RequireIdentity'
import { IdentityProvider } from '../hooks/IdentityProvider'
import { setToken } from '../hooks/identity'
import { mintTestToken } from '../../tests/support/jwt'

function renderAt(initialPath: string) {
  return render(
    <IdentityProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<div>Login Page</div>} />
          <Route element={<RequireIdentity />}>
            <Route path="/" element={<div>Protected Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </IdentityProvider>,
  )
}

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
})
