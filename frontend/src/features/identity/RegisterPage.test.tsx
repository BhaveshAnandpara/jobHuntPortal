/**
 * `/register` behavior — replaces the old `/welcome` no-password identity
 * creation flow (see `RegisterPage.tsx`'s header). Covers: creating an
 * account, persisting the token, and navigating to `/`; redirecting away
 * (without creating a duplicate account) when a token already exists; and
 * both client-side and backend `VALIDATION_ERROR` handling.
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
import { RegisterPage } from './RegisterPage'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'

function renderRegister() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <MemoryRouter initialEntries={['/register']}>
          <Routes>
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/" element={<div>Dashboard Placeholder</div>} />
          </Routes>
        </MemoryRouter>
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

describe('RegisterPage', () => {
  it('redirects to / without creating an account when a token already exists', () => {
    setToken(mintTestToken('user-existing'))
    let createCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/users`, () => {
        createCalls += 1
        return HttpResponse.json(
          {
            access_token: 'new.token',
            token_type: 'bearer',
            user: { id: 'user-new', email: 'x@example.com', display_name: 'X', created_at: '2026-01-01T00:00:00Z' },
          },
          { status: 201 },
        )
      }),
    )

    renderRegister()

    expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    expect(createCalls).toBe(0)
  })

  it('creates an account, persists the token, and navigates to / on success', async () => {
    renderRegister()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
    // The default MSW handler (tests/mocks/handlers.ts) returns access_token: 'test.jwt.token'.
    expect(localStorage.getItem('jobhunt.token')).toBe('test.jwt.token')
  })

  it('shows a client-side validation error and does not submit when email is missing', async () => {
    let createCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/users`, () => {
        createCalls += 1
        return HttpResponse.json(
          {
            access_token: 'x',
            token_type: 'bearer',
            user: { id: 'user-1', email: 'a@example.com', display_name: 'Ada', created_at: '2026-01-01T00:00:00Z' },
          },
          { status: 201 },
        )
      }),
    )
    renderRegister()

    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument()
    expect(createCalls).toBe(0)
  })

  it('shows a client-side validation error and does not submit when password is too short', async () => {
    renderRegister()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.type(screen.getByLabelText('Password'), 'short')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText(/at least 8 characters/i)).toBeInTheDocument()
  })

  it('shows an inline error and keeps the form populated on a backend 400 VALIDATION_ERROR', async () => {
    server.use(
      http.post(`${API_BASE_URL}/users`, () =>
        HttpResponse.json(
          { detail: { code: 'VALIDATION_ERROR', message: 'This email is already registered.' } },
          { status: 400 },
        ),
      ),
    )
    renderRegister()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-password-123')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('This email is already registered.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue('ada@example.com')
    expect(screen.getByLabelText('Display name')).toHaveValue('Ada Lovelace')
  })

  it('links to /login for an existing account', () => {
    renderRegister()
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
  })
})
