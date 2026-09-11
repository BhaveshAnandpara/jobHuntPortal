/**
 * `/settings` behavior — see docs/frontend/routes.md#settings--search-preferences.
 * Covers: loading existing preferences, defaulting to an empty form on a
 * 404 (unset — not an error), saving successfully, inline validation
 * errors, backend API errors on save, and a genuine load failure showing a
 * page-level error with retry.
 *
 * T10 (docs/frontend/frontend-revamp-spec.md) adds coverage for the
 * full-replace presentation: the whole form is submitted as one document,
 * validation failures keep every other entered value, and "Discard changes"
 * restores the last loaded server state.
 *
 * Owner: frontend-profile-agent.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'
import { toast } from 'sonner'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { SettingsPage } from './SettingsPage'
import { Toaster } from '../../components'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { formatDateTime } from '../../utils/format'

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

/** Sonner's toast store is module-global and replays still-active toasts to
 * the next `<Toaster>` that mounts, so a toast raised by one test would
 * otherwise show up in the next one. */
afterEach(() => {
  toast.dismiss()
})

/** `delay: null` skips user-event's inter-keystroke await — these tests type
 * into several fields per case and the default delay makes them slow enough
 * to hit the 5s test timeout. */
function typist() {
  return userEvent.setup({ delay: null })
}

describe('SettingsPage', () => {
  it('loads and displays existing preferences', async () => {
    renderSettings()

    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))
    expect(screen.getByLabelText('Target locations')).toHaveValue('Remote')
    // The loaded `remote_preference` shows on the Select's trigger. Asserted
    // on the trigger itself rather than by bare text, because Radix also
    // renders the value into a visually-hidden native <select> fallback.
    expect(screen.getByLabelText('Remote work preference')).toHaveTextContent('Remote')
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
    // The 404 is explained as "nothing set yet", not surfaced as a failure.
    expect(screen.getByRole('status')).toHaveTextContent(/haven't set any search preferences yet/i)
    expect(screen.getByRole('button', { name: 'Save preferences' })).toBeEnabled()
  })

  it('shows when preferences were last saved', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/:userId/preferences`, () =>
        HttpResponse.json({
          id: 'pref-1',
          user_id: 'user-1',
          target_roles: ['Backend Engineer'],
          target_locations: ['Remote'],
          remote_preference: 'REMOTE',
          excluded_companies: [],
          min_salary: null,
          salary_currency: null,
          updated_at: '2026-02-03T10:30:00Z',
        }),
      ),
    )
    renderSettings()

    expect(
      await screen.findByText(`Last saved ${formatDateTime('2026-02-03T10:30:00Z')}`),
    ).toBeInTheDocument()
  })

  it('submits the whole form as one full-replace PUT, including cleared fields', async () => {
    let sentBody: unknown = null
    server.use(
      http.put(`${API_BASE_URL}/users/:userId/preferences`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json({
          id: 'pref-1',
          user_id: 'user-1',
          target_roles: ['Staff Engineer'],
          target_locations: [],
          remote_preference: 'REMOTE',
          excluded_companies: [],
          min_salary: 150000,
          salary_currency: 'USD',
        })
      }),
    )
    const user = typist()
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await user.clear(screen.getByLabelText('Target roles'))
    await user.type(screen.getByLabelText('Target roles'), 'Staff Engineer')
    await user.clear(screen.getByLabelText('Target locations'))
    await user.type(screen.getByLabelText(/Minimum salary/i), '150000')
    await user.type(screen.getByLabelText(/Currency/i), 'USD')

    // Full-replace semantics are stated on the page, and there is exactly one
    // submit control — no per-field save affordance implying a partial patch.
    expect(screen.getByText(/Saving replaces every preference on this page at once/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save preferences' }))

    await waitFor(() =>
      expect(sentBody).toEqual({
        target_roles: ['Staff Engineer'],
        target_locations: [],
        remote_preference: 'REMOTE',
        excluded_companies: [],
        min_salary: 150000,
        salary_currency: 'USD',
      }),
    )
  })

  it('keeps every other entered value when one field fails validation', async () => {
    const user = typist()
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    await user.type(screen.getByLabelText('Target roles'), ', Staff Engineer')
    await user.type(screen.getByLabelText('Excluded companies'), 'Acme Corp')
    await user.type(screen.getByLabelText(/Minimum salary/i), 'not-a-number')
    await user.click(screen.getByRole('button', { name: 'Save preferences' }))

    expect(await screen.findByText('Enter a valid non-negative number.')).toBeInTheDocument()
    expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer, Staff Engineer')
    expect(screen.getByLabelText('Excluded companies')).toHaveValue('Acme Corp')
    expect(screen.getByLabelText(/Minimum salary/i)).toHaveValue('not-a-number')
    expect(screen.getByLabelText(/Minimum salary/i)).toHaveAttribute('aria-invalid', 'true')
  })

  it('restores the last loaded values when changes are discarded', async () => {
    const user = typist()
    renderSettings()
    await waitFor(() => expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer'))

    const discard = screen.getByRole('button', { name: 'Discard changes' })
    expect(discard).toBeDisabled()

    await user.type(screen.getByLabelText('Target roles'), ', Staff Engineer')
    expect(await screen.findByText('Unsaved changes')).toBeInTheDocument()
    expect(discard).toBeEnabled()

    await user.click(discard)

    expect(screen.getByLabelText('Target roles')).toHaveValue('Backend Engineer')
    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument()
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
