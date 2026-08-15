/**
 * Route: / — see docs/frontend/routes.md#--dashboard. Covers: loading,
 * empty opportunities, recent opportunities, paste-URL success/invalid/
 * ingestion-failure, and duplicate-job handling being treated uniformly
 * (no special-case crash) per
 * docs/frontend/user-flows.md#job-submission-flow.
 */

import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { DashboardPage } from './DashboardPage'

function renderDashboard() {
  setToken(mintTestToken('user-1'))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <IdentityProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/opportunities" element={<div>Opportunities Page Stub</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </IdentityProvider>,
  )
}

describe('DashboardPage', () => {
  it('loads and shows the primary action form', () => {
    renderDashboard()
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByLabelText(/paste a job posting url/i)).toBeInTheDocument()
  })

  it('shows the empty state when there are no opportunities', async () => {
    server.use(http.get(`${API_BASE_URL}/applications`, () => HttpResponse.json([])))
    renderDashboard()

    expect(await screen.findByText('No opportunities yet')).toBeInTheDocument()
    expect(screen.queryByText('Recent opportunities')).not.toBeInTheDocument()
  })

  it('shows recent opportunities and a pipeline summary when applications exist', async () => {
    renderDashboard()

    expect(await screen.findByText('Recent opportunities')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Senior Backend Engineer/i })).toBeInTheDocument()
    expect(screen.getByText('Pipeline summary')).toBeInTheDocument()
  })

  it('submits a valid job URL and navigates to the opportunities list', async () => {
    renderDashboard()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText(/paste a job posting url/i), 'https://boards.example.com/jobs/9')
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText('Opportunities Page Stub')).toBeInTheDocument()
  })

  it('shows an inline error and does not navigate for an invalid URL', async () => {
    renderDashboard()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText(/paste a job posting url/i), 'not-a-url')
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText(/must start with http/i)).toBeInTheDocument()
    expect(screen.queryByText('Opportunities Page Stub')).not.toBeInTheDocument()
  })

  it('shows the ingestion failure message inline and keeps the form populated', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/ingest-url`, () =>
        HttpResponse.json(
          { detail: { code: 'INVALID_JOB_URL', message: 'Not a recognized job posting URL' } },
          { status: 400 },
        ),
      ),
    )
    renderDashboard()
    const user = userEvent.setup()
    const input = screen.getByLabelText(/paste a job posting url/i)

    await user.type(input, 'https://boards.example.com/jobs/9')
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText('Not a recognized job posting URL')).toBeInTheDocument()
    expect(input).toHaveValue('https://boards.example.com/jobs/9')
    expect(screen.queryByText('Opportunities Page Stub')).not.toBeInTheDocument()
  })

  it('treats a duplicate submission uniformly — the backend dedupes idempotently, no special-case handling', async () => {
    // The backend transparently returns/reuses the existing Job row for a
    // duplicate URL rather than erroring; the frontend does not need to
    // detect this, it just navigates the same way as any other success.
    renderDashboard()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText(/paste a job posting url/i), 'https://boards.example.com/jobs/1')
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    await waitFor(() => expect(screen.getByText('Opportunities Page Stub')).toBeInTheDocument())
  })
})
