/**
 * `/welcome` behavior — see docs/frontend/routes.md#welcome--onboarding and
 * docs/frontend/user-flows.md#first-visit-flow. Covers: creating an
 * identity and navigating to `/`, redirecting away (without creating a
 * duplicate user) when an identity already exists, and both client-side
 * and backend `VALIDATION_ERROR` handling.
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
import { API_BASE_URL } from '../../api/client'
import { WelcomePage } from './WelcomePage'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setCurrentUserId } from '../../hooks/identity'

function renderWelcome() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <MemoryRouter initialEntries={['/welcome']}>
          <Routes>
            <Route path="/welcome" element={<WelcomePage />} />
            <Route path="/" element={<div>Dashboard Placeholder</div>} />
          </Routes>
        </MemoryRouter>
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

describe('WelcomePage', () => {
  it('redirects to / without creating a user when an identity already exists', () => {
    setCurrentUserId('user-existing')
    let createCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/users`, () => {
        createCalls += 1
        return HttpResponse.json(
          { id: 'user-new', email: 'x@example.com', display_name: 'X', created_at: '2026-01-01T00:00:00Z' },
          { status: 201 },
        )
      }),
    )

    renderWelcome()

    expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    expect(createCalls).toBe(0)
  })

  it('creates an identity, persists the id, and navigates to / on success', async () => {
    renderWelcome()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.click(screen.getByRole('button', { name: 'Create identity' }))

    await waitFor(() => expect(screen.getByText('Dashboard Placeholder')).toBeInTheDocument())
    // The default MSW handler (tests/mocks/handlers.ts) returns id: 'user-1'.
    expect(localStorage.getItem('jobhunt.userId')).toBe('user-1')
  })

  it('shows a client-side validation error and does not submit when email is missing', async () => {
    let createCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/users`, () => {
        createCalls += 1
        return HttpResponse.json(
          { id: 'user-1', email: 'a@example.com', display_name: 'Ada', created_at: '2026-01-01T00:00:00Z' },
          { status: 201 },
        )
      }),
    )
    renderWelcome()

    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.click(screen.getByRole('button', { name: 'Create identity' }))

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument()
    expect(createCalls).toBe(0)
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
    renderWelcome()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Display name'), 'Ada Lovelace')
    await userEvent.click(screen.getByRole('button', { name: 'Create identity' }))

    expect(await screen.findByText('This email is already registered.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue('ada@example.com')
    expect(screen.getByLabelText('Display name')).toHaveValue('Ada Lovelace')
  })

  it('never implies real authentication (no password field or login control)', () => {
    renderWelcome()
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /log ?in|sign ?in/i })).not.toBeInTheDocument()
    // The page positively reassures the user this isn't a real account/session.
    expect(screen.getByText(/no password/i)).toBeInTheDocument()
  })
})
