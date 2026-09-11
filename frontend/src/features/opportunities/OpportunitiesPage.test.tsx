/**
 * Route: /opportunities — see
 * docs/frontend/routes.md#opportunities--opportunities. Covers: status-tab
 * partitioning, the two distinct empty states (no opportunities at all vs.
 * no match for the current filter), row navigation, and the optimistic
 * "submitted, analyzing…" banner from the job-submission flow.
 *
 * T7 (docs/frontend/frontend-revamp-spec.md) added coverage for the two
 * acceptance criteria that are behavioral rather than visual — switching
 * tabs never flashes the page-level loading state, and the mobile card
 * collapse renders alongside the desktop table — plus the per-tab counts and
 * the no-analyzed-resume variant of the "nothing at all" empty state.
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
  setToken(mintTestToken('user-1'))
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
            <Route path="/resumes" element={<div>Resumes Page Stub</div>} />
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
    // This state has no filter to clear — that's what makes it a different
    // state from the per-tab one, not just different wording.
    expect(screen.queryByRole('button', { name: 'Show all opportunities' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('points a brand-new account at /resumes when no analyzed resume exists yet', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
    )
    renderOpportunities()

    expect(await screen.findByText('Add a resume to get started')).toBeInTheDocument()
    expect(screen.queryByText('No opportunities yet')).not.toBeInTheDocument()
    const links = screen.getAllByRole('link', { name: 'Upload a resume' })
    expect(links.length).toBeGreaterThan(0)
    expect(links[0]).toHaveAttribute('href', '/resumes')
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

    await user.click(screen.getByRole('tab', { name: /^Applied/ }))
    expect(await screen.findByText('No opportunities in this status')).toBeInTheDocument()
    // Names the filter and how many rows live outside it — not a restatement
    // of the empty-account copy.
    expect(screen.getByText(/None of your 1 opportunity is in Applied right now\./)).toBeInTheDocument()
    expect(screen.queryByText('No opportunities yet')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show all opportunities' }))
    await waitFor(() => expect(screen.getAllByText('Senior Backend Engineer').length).toBeGreaterThan(0))
  })

  it('labels each tab with the real number of rows in that status group', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([
          applicationFixture({ id: 'app-1', job_id: 'job-1', status: 'SHORTLISTED' }),
          applicationFixture({ id: 'app-2', job_id: 'job-2', status: 'APPLIED', title: 'Data Engineer' }),
          applicationFixture({ id: 'app-3', job_id: 'job-3', status: 'REJECTED', title: 'SRE' }),
        ]),
      ),
    )
    renderOpportunities()

    expect(await screen.findByRole('tab', { name: 'Active 1' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Applied 1' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Closed 1' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'All 3' })).toBeInTheDocument()
  })

  it('switches tabs without re-showing the page-level loading state', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([
          applicationFixture({ id: 'app-1', job_id: 'job-1', status: 'SHORTLISTED' }),
          applicationFixture({
            id: 'app-2',
            job_id: 'job-2',
            status: 'APPLIED',
            title: 'Data Engineer',
          }),
        ]),
      ),
    )
    renderOpportunities()
    const user = userEvent.setup()

    await waitFor(() => expect(screen.getAllByText('Senior Backend Engineer').length).toBeGreaterThan(0))

    await user.click(screen.getByRole('tab', { name: /^Applied/ }))

    // The partition is client-side over one already-resolved query, so the
    // filtered row is on screen the moment the tab changes — no skeleton in
    // between (T7's "no full-page loading flash" criterion).
    expect(screen.queryByRole('status', { name: /loading opportunities/i })).not.toBeInTheDocument()
    expect(screen.getAllByText('Data Engineer').length).toBeGreaterThan(0)
    expect(screen.queryByText('Senior Backend Engineer')).not.toBeInTheDocument()
  })

  it('renders both the desktop table and the collapsed mobile card list for the same rows', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([applicationFixture({ id: 'app-1', status: 'SHORTLISTED' })]),
      ),
    )
    renderOpportunities()

    // `Table` (src/components/Table.tsx) renders both and lets CSS pick one
    // per viewport: the <table> is `hidden md:block`, the card <ul> is
    // `md:hidden`. Asserting both exist is what proves the ~768px collapse is
    // wired up at all — the breakpoint itself is CSS, covered by
    // tests/e2e/responsive-accessibility.spec.ts at a real mobile viewport.
    expect(await screen.findByRole('table', { name: 'Opportunities' })).toBeInTheDocument()
    const cardList = screen.getByRole('list', { name: 'Opportunities' })
    expect(cardList).toHaveClass('md:hidden')
    expect(cardList).toHaveTextContent('Senior Backend Engineer')
    // The card layout keeps the column headers as per-field labels, so no
    // column is silently dropped on mobile.
    expect(cardList).toHaveTextContent('Last activity')
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

  it('orders rows by last activity, most recent first', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([
          applicationFixture({
            id: 'app-1',
            job_id: 'job-1',
            title: 'Older Opportunity',
            updated_at: '2026-01-01T00:00:00Z',
          }),
          applicationFixture({
            id: 'app-2',
            job_id: 'job-2',
            title: 'Newer Opportunity',
            updated_at: '2026-02-01T00:00:00Z',
          }),
        ]),
      ),
    )
    renderOpportunities()

    const table = await screen.findByRole('table', { name: 'Opportunities' })
    await waitFor(() => expect(table).toHaveTextContent('Newer Opportunity'))
    const rowTitles = Array.from(table.querySelectorAll('tbody tr')).map(
      (row) => row.textContent ?? '',
    )
    expect(rowTitles[0]).toContain('Newer Opportunity')
    expect(rowTitles[1]).toContain('Older Opportunity')
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
