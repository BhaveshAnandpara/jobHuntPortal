/**
 * `/settings` behavior — see docs/frontend/routes.md#settings--search-preferences.
 * Covers: loading existing preferences, defaulting to an empty form on a
 * 404 (unset — not an error), saving successfully, inline validation
 * errors, backend API errors on save, and a genuine load failure showing a
 * page-level error with retry.
 *
 * Owner: frontend-profile-agent.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { SettingsPage } from './SettingsPage'
import { Toaster } from '../../components'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'

function renderSettings() {
  setToken(mintTestToken('user-1'))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <Toaster />
        <SettingsPage />
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

describe('SettingsPage', () => {
  it('loads and displays existing preferences', async () => {
    renderSettings()

    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))
    expect(screen.getByLabelText('Target locations')).toHaveValue('Remote')
    expect(screen.getByText('Remote')).toBeInTheDocument() // Select's rendered value label
  })

  it('shows an empty/default form (not an error) when preferences are unset (404)', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/:userId/preferences`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'No preferences set' } }, { status: 404 }),
      ),
    )

    renderSettings()

    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue(''))
    expect(screen.getByLabelText('Target locations')).toHaveValue('')
    // Radix Select renders "Not set" both in its visible trigger and in a
    // visually-hidden native <select> fallback for form semantics — assert
    // at least one is present rather than requiring exactly one.
    expect(screen.getAllByText('Not set').length).toBeGreaterThan(0)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('saves successfully and shows a success toast', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await userEvent.click(screen.getByRole('button', { name: 'Save preferences' }))

    expect(await screen.findByText('Preferences saved.')).toBeInTheDocument()
  })

  it('shows an inline validation error on a 400 VALIDATION_ERROR without discarding form input', async () => {
    server.use(
      http.put(`${API_BASE_URL}/users/:userId/preferences`, () =>
        HttpResponse.json(
          { detail: { code: 'VALIDATION_ERROR', message: 'min_salary must be non-negative' } },
          { status: 400 },
        ),
      ),
    )
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await userEvent.click(screen.getByRole('button', { name: 'Save preferences' }))

    expect(await screen.findByText('min_salary must be non-negative')).toBeInTheDocument()
    expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer')
  })

  it('shows a client-side validation error for a non-numeric minimum salary', async () => {
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await userEvent.type(screen.getByLabelText(/Minimum salary/i), 'not-a-number')
    await userEvent.click(screen.getByRole('button', { name: 'Save preferences' }))

    expect(await screen.findByText('Enter a valid non-negative number.')).toBeInTheDocument()
  })

  it('shows a toast on a non-validation API error during save', async () => {
    server.use(
      http.put(`${API_BASE_URL}/users/:userId/preferences`, () =>
        HttpResponse.json({ detail: { code: 'INTERNAL_ERROR', message: 'Something broke.' } }, { status: 500 }),
      ),
    )
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await userEvent.click(screen.getByRole('button', { name: 'Save preferences' }))

    expect(await screen.findByText('Something broke.')).toBeInTheDocument()
  })

  it('shows a page-level error with retry when loading preferences genuinely fails', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/:userId/preferences`, () =>
        HttpResponse.json({ detail: { code: 'INTERNAL_ERROR', message: 'Preferences unavailable.' } }, { status: 500 }),
      ),
    )
    renderSettings()

    expect(await screen.findByText('Preferences unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
