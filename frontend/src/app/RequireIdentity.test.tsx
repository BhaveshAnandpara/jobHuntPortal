/**
 * `<RequireIdentity>` redirect-logic tests — see this repo's
 * `docs/frontend/agent-ownership.md#frontend-shell-agent` testing
 * responsibility list. Verifies the guard redirects to `/welcome` when no
 * identity is persisted, and renders the protected route's `<Outlet />`
 * once one exists — the counterpart to `/welcome` itself redirecting to
 * `/` on success, which is `frontend-profile-agent`'s `WelcomePage`
 * responsibility (Wave 2), not tested here.
 *
 * Owner: frontend-shell-agent.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { RequireIdentity } from './RequireIdentity'
import { IdentityProvider } from '../hooks/IdentityProvider'
import { setCurrentUserId } from '../hooks/identity'

function renderAt(initialPath: string) {
  return render(
    <IdentityProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/welcome" element={<div>Welcome Page</div>} />
          <Route element={<RequireIdentity />}>
            <Route path="/" element={<div>Protected Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </IdentityProvider>,
  )
}

describe('RequireIdentity', () => {
  it('redirects to /welcome when no identity is persisted', () => {
    renderAt('/')
    expect(screen.getByText('Welcome Page')).toBeInTheDocument()
    expect(screen.queryByText('Protected Dashboard')).not.toBeInTheDocument()
  })

  it('renders the protected route when an identity is persisted', () => {
    setCurrentUserId('user-123')
    renderAt('/')
    expect(screen.getByText('Protected Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Welcome Page')).not.toBeInTheDocument()
  })
})
