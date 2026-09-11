/**
 * `/login` behavior — see `LoginPage.tsx`'s header. Covers: logging in and
 * navigating to `/`, redirecting away when a token already exists, and
 * showing the backend's normalized message on a 401 UNAUTHORIZED (wrong
 * credentials).
 *
 * Owner: frontend-profile-agent.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { delay, http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { mintTestToken } from '../../../tests/support/jwt'
import { API_BASE_URL } from '../../api/client'
import { LoginPage } from './LoginPage'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'

function renderLogin(initialEntry: string | { pathname: string; state: unknown } = '/login') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<div>Dashboard Placeholder</div>} />
            <Route path="/settings" element={<div>Settings Placeholder</div>} />
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

  // T4 (docs/frontend/frontend-revamp-spec.md): the three states this screen
  // must cover explicitly — error, loading, success — plus the anti-ui-slop
  // rule that an error never discards what the user typed.

  it('keeps every entered value and stays on the form after a wrong password', async () => {
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

    // The failure is an inline, form-level banner (not a toast) that names
    // what failed, and it sits next to the form the user must fix.
    const banner = await screen.findByRole('alert')
    expect(within(banner).getByText("Couldn't log you in")).toBeInTheDocument()
    expect(within(banner).getByText('invalid email or password')).toBeInTheDocument()

    // Nothing the user typed is discarded.
    expect(screen.getByLabelText('Email')).toHaveValue('ada@example.com')
    expect(screen.getByLabelText('Password')).toHaveValue('wrong-password')
    expect(screen.queryByText('Dashboard Placeholder')).not.toBeInTheDocument()
  })

  it('shows inline field errors without calling the API when the email is invalid', async () => {
    let loginCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/auth/login`, () => {
        loginCalls += 1
        return HttpResponse.json({
          access_token: 'x',
          token_type: 'bearer',
          user: { id: 'user-1', email: 'a@example.com', display_name: 'A', created_at: '2026-01-01T00:00:00Z' },
        })
      }),
    )
    renderLogin()

    await userEvent.type(screen.getByLabelText('Email'), 'not-an-email')
    await userEvent.type(screen.getByLabelText('Password'), 'some-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument()
    expect(loginCalls).toBe(0)
    // The invalid control is marked up as such and points at its own message.
    const emailInput = screen.getByLabelText('Email')
    expect(emailInput).toHaveAttribute('aria-invalid', 'true')
    expect(emailInput).toHaveAttribute('aria-describedby', 'email-error')
    // ...and the entered values survive a validation failure too.
    expect(emailInput).toHaveValue('not-an-email')
    expect(screen.getByLabelText('Password')).toHaveValue('some-password')
  })

  it('disables the submit button and marks it busy while the login request is in flight', async () => {
    server.use(
      http.post(`${API_BASE_URL}/auth/login`, async () => {
        await delay(50)
        return HttpResponse.json({
          access_token: 'pending.token',
          token_type: 'bearer',
          user: { id: 'user-1', email: 'ada@example.com', display_name: 'Ada', created_at: '2026-01-01T00:00:00Z' },
        })
      }),
    )
    renderLogin()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    const submit = screen.getByRole('button', { name: 'Log in' })
    await waitFor(() => expect(submit).toBeDisabled())
    expect(submit).toHaveAttribute('aria-busy', 'true')

    // ...and it resolves into the success state rather than staying pending.
    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
    expect(localStorage.getItem('jobhunt.token')).toBe('pending.token')
  })

  it('returns the user to the protected route they were bounced off, when one was carried in', async () => {
    // `app/RequireIdentity` (T3) redirects with state `{ from: '/settings' }`.
    renderLogin({ pathname: '/login', state: { from: '/settings' } })

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(screen.getByText('Settings Placeholder')).toBeInTheDocument())
  })

  it('ignores an off-site redirect target and lands on / instead', async () => {
    renderLogin({ pathname: '/login', state: { from: '//evil.example.com' } })

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
  })

  it('clears a previous server error when the form is resubmitted', async () => {
    let attempt = 0
    server.use(
      http.post(`${API_BASE_URL}/auth/login`, () => {
        attempt += 1
        if (attempt === 1) {
          return HttpResponse.json(
            { detail: { code: 'UNAUTHORIZED', message: 'invalid email or password' } },
            { status: 401 },
          )
        }
        return HttpResponse.json({
          access_token: 'second.token',
          token_type: 'bearer',
          user: { id: 'user-1', email: 'ada@example.com', display_name: 'Ada', created_at: '2026-01-01T00:00:00Z' },
        })
      }),
    )
    renderLogin()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByText('invalid email or password')).toBeInTheDocument()

    // Fix just the password — the email is still there from the first try.
    await userEvent.clear(screen.getByLabelText('Password'))
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
    expect(screen.queryByText('invalid email or password')).not.toBeInTheDocument()
    expect(localStorage.getItem('jobhunt.token')).toBe('second.token')
  })
})
