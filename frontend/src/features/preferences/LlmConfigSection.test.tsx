/**
 * `/settings` → AI provider section — see docs/llm-provider-config/PRODUCT.md.
 *
 * Colocated in its own file rather than `SettingsPage.test.tsx` because the
 * component is its own form against its own endpoint: these cases are about
 * `PUT /users/me/llm-config`, never about the six preference fields. The
 * harness (MSW defaults + `server.use` overrides per error path, a minted
 * token, a fresh QueryClient) is deliberately identical to that file's.
 *
 * Covers: loading, an existing config, the unset (404) case rendering an
 * empty form rather than an error, the write-only key field, the masked hint
 * shown beside (never inside) that field, a rejected key rendering inline,
 * and a successful save clearing the key field and refreshing the hint.
 *
 * Owner: frontend-profile-agent.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'
import { toast } from 'sonner'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { LlmConfigSection } from './LlmConfigSection'
import { Toaster } from '../../components'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'

function renderSection() {
  setToken(mintTestToken('user-1'))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <Toaster />
        <LlmConfigSection />
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

/** Sonner's toast store is module-global and replays still-active toasts
 * into the next mounted `<Toaster>`. */
afterEach(() => {
  toast.dismiss()
})

function unsetConfig() {
  server.use(
    http.get(`${API_BASE_URL}/users/me/llm-config`, () =>
      HttpResponse.json(
        { detail: { code: 'NOT_FOUND', message: 'No LLM config set' } },
        { status: 404 },
      ),
    ),
  )
}

async function chooseProvider(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole('combobox', { name: 'AI provider' }))
  await user.click(await screen.findByRole('option', { name: label }))
}

describe('LlmConfigSection', () => {
  it('shows a loading state while the config is being fetched', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/me/llm-config`, async () => {
        await delay(50)
        return HttpResponse.json({
          provider: 'groq',
          model: null,
          key_hint: '...ab12',
          last_validated_at: null,
        })
      }),
    )
    renderSection()

    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument()
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })

  it('loads an existing configuration into the form', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/me/llm-config`, () =>
        HttpResponse.json({
          provider: 'openai',
          model: 'gpt-4o-mini',
          key_hint: '...ab12',
          last_validated_at: '2026-03-01T09:00:00Z',
        }),
      ),
    )
    renderSection()

    await waitFor(() => expect(screen.getByLabelText(/Model/)).toHaveValue('gpt-4o-mini'))
    expect(screen.getByRole('combobox', { name: 'AI provider' })).toHaveTextContent('OpenAI')
  })

  it('never pre-fills the key field, and shows the masked hint beside it instead', async () => {
    renderSection()

    const keyInput = await screen.findByLabelText('API key')
    // Write-only: empty value, password type, and the hint is nowhere near
    // the input's own value or placeholder.
    expect(keyInput).toHaveValue('')
    expect(keyInput).toHaveAttribute('type', 'password')
    expect(keyInput.getAttribute('placeholder')).not.toContain('ab12')
    expect(screen.getByText('Current key ...ab12')).toBeInTheDocument()
  })

  it('renders an empty form (not an error) when no provider is configured', async () => {
    unsetConfig()
    renderSection()

    expect(await screen.findByText(/haven't added an AI provider key yet/i)).toBeInTheDocument()
    expect(screen.getByLabelText('API key')).toHaveValue('')
    expect(screen.getByLabelText(/Model/)).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'AI provider' })).toHaveTextContent('Select a provider')
    expect(screen.queryByText(/Current key/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save AI provider' })).toBeEnabled()
  })

  it('requires a provider and a key before it will submit', async () => {
    unsetConfig()
    let called = false
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, () => {
        called = true
        return HttpResponse.json({ provider: 'claude', model: null, key_hint: '...wxyz' })
      }),
    )
    renderSection()
    await screen.findByLabelText('API key')

    await userEvent.click(screen.getByRole('button', { name: 'Save AI provider' }))

    expect(await screen.findByText('Choose an AI provider.')).toBeInTheDocument()
    expect(
      screen.getByText('Enter your API key — saving re-checks it with the provider.'),
    ).toBeInTheDocument()
    expect(called).toBe(false)
  })

  it('submits the chosen provider, an optional model, and the typed key', async () => {
    unsetConfig()
    let sentBody: unknown = null
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json({
          provider: 'claude',
          model: null,
          key_hint: '...wxyz',
          last_validated_at: '2026-03-02T09:00:00Z',
        })
      }),
    )
    const user = userEvent.setup({ delay: null })
    renderSection()
    await screen.findByLabelText('API key')

    await chooseProvider(user, 'Claude (Anthropic)')
    await user.type(screen.getByLabelText('API key'), 'sk-ant-secret-wxyz')
    await user.click(screen.getByRole('button', { name: 'Save AI provider' }))

    await waitFor(() =>
      expect(sentBody).toEqual({
        provider: 'claude',
        // An untouched model field submits `null`, not `""` — the backend
        // reads null as "use the provider default".
        model: null,
        api_key: 'sk-ant-secret-wxyz',
      }),
    )
  })

  it('sends a model override when one is typed', async () => {
    let sentBody: unknown = null
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json({ provider: 'groq', model: 'llama-3.3-70b', key_hint: '...ab12' })
      }),
    )
    const user = userEvent.setup({ delay: null })
    renderSection()
    await screen.findByLabelText('API key')

    await user.type(screen.getByLabelText(/Model/), 'llama-3.3-70b')
    await user.type(screen.getByLabelText('API key'), 'gsk-secret-ab12')
    await user.click(screen.getByRole('button', { name: 'Save AI provider' }))

    await waitFor(() =>
      expect(sentBody).toEqual({
        provider: 'groq',
        model: 'llama-3.3-70b',
        api_key: 'gsk-secret-ab12',
      }),
    )
  })

  it('clears the key field, toasts, and refreshes the masked hint after a successful save', async () => {
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, () =>
        HttpResponse.json({
          provider: 'groq',
          model: null,
          key_hint: '...9999',
          last_validated_at: '2026-03-02T09:00:00Z',
        }),
      ),
    )
    const user = userEvent.setup({ delay: null })
    renderSection()
    await screen.findByLabelText('API key')
    expect(screen.getByText('Current key ...ab12')).toBeInTheDocument()

    await user.type(screen.getByLabelText('API key'), 'gsk-brand-new-9999')
    await user.click(screen.getByRole('button', { name: 'Save AI provider' }))

    expect(await screen.findByText('AI provider saved.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('API key')).toHaveValue(''))
    expect(screen.getByText('Current key ...9999')).toBeInTheDocument()
  })

  it('renders a rejected key inline, keeping the rest of the form intact', async () => {
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, () =>
        HttpResponse.json(
          {
            detail: {
              code: 'VALIDATION_ERROR',
              message: 'This key was rejected by Groq (invalid_api_key) — check it and try again',
            },
          },
          { status: 400 },
        ),
      ),
    )
    const user = userEvent.setup({ delay: null })
    renderSection()
    await screen.findByLabelText('API key')

    await user.type(screen.getByLabelText(/Model/), 'llama-3.3-70b')
    await user.type(screen.getByLabelText('API key'), 'gsk-wrong')
    await user.click(screen.getByRole('button', { name: 'Save AI provider' }))

    const banner = await screen.findByRole('alert')
    expect(
      within(banner).getByText(
        'This key was rejected by Groq (invalid_api_key) — check it and try again',
      ),
    ).toBeInTheDocument()
    // A failed save keeps what the user typed so they can correct it.
    expect(screen.getByLabelText(/Model/)).toHaveValue('llama-3.3-70b')
    expect(screen.getByLabelText('API key')).toHaveValue('gsk-wrong')
  })

  it('toasts a non-validation failure rather than rendering it inline', async () => {
    server.use(
      http.put(`${API_BASE_URL}/users/me/llm-config`, () =>
        HttpResponse.json(
          { detail: { code: 'INTERNAL_ERROR', message: 'Something broke.' } },
          { status: 500 },
        ),
      ),
    )
    const user = userEvent.setup({ delay: null })
    renderSection()
    await screen.findByLabelText('API key')

    await user.type(screen.getByLabelText('API key'), 'gsk-secret')
    await user.click(screen.getByRole('button', { name: 'Save AI provider' }))

    expect(await screen.findByText('Something broke.')).toBeInTheDocument()
  })

  it('keeps the form usable when the config genuinely fails to load', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/me/llm-config`, () =>
        HttpResponse.json(
          { detail: { code: 'INTERNAL_ERROR', message: 'AI provider settings unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderSection()

    expect(await screen.findByText('AI provider settings unavailable.')).toBeInTheDocument()
    expect(screen.getByLabelText('API key')).toBeInTheDocument()
  })
})
