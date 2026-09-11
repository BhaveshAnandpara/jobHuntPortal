/**
 * Route: / — see docs/frontend/routes.md#--dashboard. Covers: loading,
 * empty opportunities, recent opportunities, paste-URL success/invalid/
 * ingestion-failure, and duplicate-job handling being treated uniformly
 * (no special-case crash) per
 * docs/frontend/user-flows.md#job-submission-flow.
 *
 * T5 (docs/frontend/frontend-revamp-spec.md) added coverage for the states
 * that ticket introduced: the brand-new-account onboarding empty state
 * (zero analyzed resumes -> point at `/resumes` instead of a pipeline of
 * zeros), the summary-card loading skeletons, the pipeline counts matching
 * `GET /applications`, the needs-attention rows deep linking to
 * `/outreach/:outreachId`, and the `INVALID_JOB_URL`-specific inline hint.
 */

import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
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
            <Route path="/resumes" element={<div>Resumes Page Stub</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </IdentityProvider>,
  )
}

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

/** Zero ACTIVE profiles === no analyzed resume — see jobSubmissionReadiness.ts. */
function withNoAnalyzedResume() {
  server.use(http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])))
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
    // Scoped to the Recent section: the needs-attention list now names the
    // same job too (it joins outreach.job_id to the application row), so an
    // unscoped role query would legitimately match two links.
    const recent = within(screen.getByRole('region', { name: 'Recent opportunities' }))
    expect(recent.getByRole('link', { name: /Senior Backend Engineer/i })).toBeInTheDocument()
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

  it('adds an INVALID_JOB_URL-specific hint under the input, and only for that code', async () => {
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

    await user.type(
      screen.getByLabelText(/paste a job posting url/i),
      'https://boards.example.com/jobs/9',
    )
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText(/check the link opens the posting itself/i)).toBeInTheDocument()
  })

  it('shows a non-INVALID_JOB_URL ingestion failure inline without the URL-format hint', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/ingest-url`, () =>
        HttpResponse.json(
          { detail: { code: 'JOB_FETCH_FAILED', message: 'Could not fetch the job posting.' } },
          { status: 400 },
        ),
      ),
    )
    renderDashboard()
    const user = userEvent.setup()

    await user.type(
      screen.getByLabelText(/paste a job posting url/i),
      'https://boards.example.com/jobs/9',
    )
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText('Could not fetch the job posting.')).toBeInTheDocument()
    expect(screen.queryByText(/check the link opens the posting itself/i)).not.toBeInTheDocument()
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

  it('points a brand-new account at /resumes instead of showing an empty pipeline', async () => {
    withNoAnalyzedResume()
    server.use(http.get(`${API_BASE_URL}/applications`, () => HttpResponse.json([])))
    renderDashboard()

    expect(await screen.findByText('Add a resume to get started')).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: 'Upload a resume' })
    expect(cta).toHaveAttribute('href', '/resumes')

    // The URL form is not the primary action for this account yet, and a
    // pipeline of zeros is not shown in place of real guidance.
    expect(screen.queryByLabelText(/paste a job posting url/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Pipeline summary')).not.toBeInTheDocument()
    expect(screen.queryByText('No opportunities yet')).not.toBeInTheDocument()
  })

  it('still shows the pipeline when the resume CTA applies but opportunities already exist', async () => {
    withNoAnalyzedResume()
    renderDashboard()

    expect(await screen.findByText('Add a resume to get started')).toBeInTheDocument()
    expect(await screen.findByText('Pipeline summary')).toBeInTheDocument()
    expect(screen.getByText('Recent opportunities')).toBeInTheDocument()
  })

  it('shows summary-card skeletons while GET /applications is pending', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, async () => {
        await delay('infinite')
        return HttpResponse.json([])
      }),
    )
    renderDashboard()

    expect(await screen.findByRole('status', { name: /loading pipeline summary/i })).toBeInTheDocument()
  })

  it('derives the pipeline counts from the GET /applications rows', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications`, () =>
        HttpResponse.json([
          applicationFixture({ id: 'app-1', status: 'SHORTLISTED' }),
          applicationFixture({ id: 'app-2', job_id: 'job-2', status: 'CONTACT_SEARCH' }),
          applicationFixture({ id: 'app-3', job_id: 'job-3', status: 'INTERVIEW' }),
          applicationFixture({ id: 'app-4', job_id: 'job-4', status: 'REJECTED' }),
        ]),
      ),
    )
    renderDashboard()

    // The summary section is on screen immediately (it renders the loading
    // skeletons first), so wait for a real card before reading the counts.
    await screen.findByText('Active')
    const summary = within(screen.getByRole('region', { name: 'Pipeline summary' }))
    // Each count sits in the same card as its label, so the assertion is
    // scoped to that card rather than to a bare number anywhere on the page.
    const cardFor = (label: string) => summary.getByText(label).closest('div') as HTMLElement
    expect(within(cardFor('Active')).getByText('2')).toBeInTheDocument()
    expect(within(cardFor('Applied')).getByText('1')).toBeInTheDocument()
    expect(within(cardFor('Closed')).getByText('1')).toBeInTheDocument()
  })

  it('links each needs-attention row straight to its outreach review page', async () => {
    renderDashboard()

    const attention = within(await screen.findByRole('region', { name: 'Needs your review' }))
    const link = attention.getByRole('link', { name: /Senior Backend Engineer/i })
    expect(link).toHaveAttribute('href', '/outreach/outreach-1')
    // The job name comes from joining outreach.job_id to the applications
    // response — OutreachResponse itself carries no company/title.
    expect(attention.getByText('Acme Robotics — Senior Backend Engineer')).toBeInTheDocument()
  })

  it('renders no needs-attention section when nothing is awaiting approval', async () => {
    server.use(http.get(`${API_BASE_URL}/outreach`, () => HttpResponse.json([])))
    renderDashboard()

    expect(await screen.findByText('Pipeline summary')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Needs your review' })).not.toBeInTheDocument()
  })

  it('keeps the rest of the page usable when GET /outreach fails', async () => {
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () =>
        HttpResponse.json(
          { detail: { code: 'INTERNAL_ERROR', message: 'Outreach is unavailable right now.' } },
          { status: 500 },
        ),
      ),
    )
    renderDashboard()

    expect(await screen.findByText('Outreach is unavailable right now.')).toBeInTheDocument()
    expect(await screen.findByText('Pipeline summary')).toBeInTheDocument()
    expect(screen.getByText('Recent opportunities')).toBeInTheDocument()
  })
})
