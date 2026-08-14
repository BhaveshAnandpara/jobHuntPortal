/**
 * Route: /opportunities — see
 * docs/frontend/routes.md#opportunities--opportunities. Covers: status-tab
 * partitioning, the two distinct empty states (no opportunities at all vs.
 * no match for the current filter), row navigation, and the optimistic
 * "submitted, analyzing…" banner from the job-submission flow.
 */

import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { setCurrentUserId } from '../../hooks/identity'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { OpportunitiesPage } from './OpportunitiesPage'

function applicationFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'app-1',
    job_id: 'job-1',
    user_id: 'user-1',
    company: 'Acme Robotics',
    title: 'Senior Backend Engineer',
    status: 'SHORTLISTED',
    selected_resume_id: 'resume-1',
    match_score: 0.87,
    matched_skills: ['Python'],
    missing_skills: ['Kafka'],
    discovered_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function renderOpportunities(initialEntries: Array<string | { pathname: string; state?: unknown }> = ['/opportunities']) {
  setCurrentUserId('user-1')
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <IdentityProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/opportunities" element={<OpportunitiesPage />} />
            <Route path="/opportunities/:applicationId" element={<div>Detail Page Stub</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </IdentityProvider>,
  )
}

describe('OpportunitiesPage', () => {
  it('shows the "no opportunities at all" empty state when the list is empty', async () => {
    server.use(http.get(`${API_BASE_URL}/applications`, () => HttpResponse.json([])))
    renderOpportunities()

    expect(await screen.findByText('No opportunities yet')).toBeInTheDocument()
  })

  it('partitions applications into status tabs and shows a distinct "no match" empty state per filter', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([applicationFixture({ id: 'app-1', status: 'SHORTLISTED' })]),
      ),
    )
    renderOpportunities()
    const user = userEvent.setup()

    // The Table primitive renders both a desktop <table> and a mobile card
    // list simultaneously (CSS-only hiding), so row text appears twice in
    // jsdom — assert presence via getAllByText rather than the singular
    // (multiple-match-intolerant) getByText/findByText.
    await waitFor(() => expect(screen.getAllByText('Senior Backend Engineer').length).toBeGreaterThan(0))

    await user.click(screen.getByRole('tab', { name: 'Applied' }))
    expect(await screen.findByText('No opportunities match this filter')).toBeInTheDocument()
    expect(screen.queryByText('No opportunities yet')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filter' }))
    await waitFor(() => expect(screen.getAllByText('Senior Backend Engineer').length).toBeGreaterThan(0))
  })

  it('navigates to the detail page when a row is clicked', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([applicationFixture({ id: 'app-1', status: 'SHORTLISTED' })]),
      ),
    )
    renderOpportunities()
    const user = userEvent.setup()

    await waitFor(() => expect(screen.getAllByText('Senior Backend Engineer').length).toBeGreaterThan(0))
    const [row] = screen.getAllByText('Senior Backend Engineer')
    await user.click(row)

    expect(await screen.findByText('Detail Page Stub')).toBeInTheDocument()
  })

  it('shows an optimistic submitted-job banner until the real application row appears', async () => {
    renderOpportunities([
      {
        pathname: '/opportunities',
        state: { submittedJob: { jobId: 'job-9', company: 'NewCo', title: 'Platform Engineer' } },
      },
    ])

    await waitFor(() => expect(screen.getByText(/NewCo/)).toBeInTheDocument())
    expect(screen.getByText(/submitted\. analyzing/i)).toBeInTheDocument()
  })
})
