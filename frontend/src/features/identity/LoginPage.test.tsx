/**
 * `/login` behavior — see `LoginPage.tsx`'s header. Covers: logging in and
 * navigating to `/`, redirecting away when a token already exists, and
 * showing the backend's normalized message on a 401 UNAUTHORIZED (wrong
 * credentials).
 *
 * Owner: frontend-profile-agent.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { mintTestToken } from '../../../tests/support/jwt'
import { API_BASE_URL } from '../../api/client'
import { LoginPage } from './LoginPage'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'

function renderLogin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <MemoryRouter initialEntries={['/login']}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<div>Dashboard Placeholder</div>} />
          </Routes>
        </MemoryRouter>
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

describe('LoginPage', () => {
  it('redirects to / without calling login when a token already exists', () => {
    setToken(mintTestToken('user-existing'))
    let loginCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/auth/login`, () => {
        loginCalls += 1
        return HttpResponse.json({
          access_token: 'new.token',
          token_type: 'bearer',
          user: { id: 'user-existing', email: 'x@example.com', display_name: 'X', created_at: '2026-01-01T00:00:00Z' },
        })
      }),
    )

    renderLogin()

    expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    expect(loginCalls).toBe(0)
  })

  it('logs in, persists the token, and navigates to / on success', async () => {
    renderLogin()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
    // The default MSW handler (tests/mocks/handlers.ts) returns access_token: 'test.jwt.token'.
    expect(localStorage.getItem('jobhunt.token')).toBe('test.jwt.token')
  })

  it('shows the backend message inline on a 401 and does not navigate', async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/login`, () =>
        HttpResponse.json(
          { detail: { code: 'UNAUTHORIZED', message: 'invalid email or password' } },
          { status: 401 },
        ),
      ),
    )
    renderLogin()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByText('invalid email or password')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard Placeholder')).not.toBeInTheDocument()
  })

  it('links to /register for a new account', () => {
    renderLogin()
    expect(screen.getByRole('link', { name: 'Create one' })).toHaveAttribute('href', '/register')
  })
})
