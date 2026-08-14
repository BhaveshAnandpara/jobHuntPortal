/**
 * Route: /opportunities/:applicationId — see
 * docs/frontend/routes.md#opportunitiesapplicationid--opportunity-detail.
 * Covers: full `JobResponse` rendering (Step 10.5 fields), the selected
 * resume panel, the `profile_scores` resume-comparison panel (previously
 * blocked, now unblocked — a core scenario, not skipped), the lifecycle
 * timeline, a valid manual status update, a 409 rejection showing the error
 * and refetching (not forcing state locally), the wired `ContactsPanel`
 * integration against the locked `{ jobId }` contract, and processing
 * polling advancing panels across statuses until the poll's documented
 * stop condition.
 */

import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { setCurrentUserId } from '../../hooks/identity'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { OpportunityDetailPage } from './OpportunityDetailPage'

// frontend-contacts-agent's ContactsPanel is built in parallel against the
// same locked `{ jobId }` contract — stubbed here so this test proves the
// integration point (this page renders <ContactsPanel jobId={...} /> wired
// to the right job id) without depending on that component's own internals.
vi.mock('../contacts/ContactsPanel', () => ({
  ContactsPanel: ({ jobId }: { jobId: string }) => (
    <div data-testid="contacts-panel-stub">Contacts for {jobId}</div>
  ),
}))

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

function renderDetail(applicationId = 'app-1') {
  setCurrentUserId('user-1')
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <IdentityProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/opportunities/${applicationId}`]}>
          <Routes>
            <Route path="/opportunities/:applicationId" element={<OpportunityDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </IdentityProvider>,
  )
}

describe('OpportunityDetailPage', () => {
  it('renders the full Job Information panel from JobResponse (Step 10.5 fields)', async () => {
    renderDetail()

    // Wait on data-dependent content, not the panel heading — the heading
    // renders during the loading/skeleton state too.
    expect(await screen.findByText('Remote')).toBeInTheDocument()
    expect(screen.getByText('Job information')).toBeInTheDocument()
    expect(screen.getByText('Own our platform services.')).toBeInTheDocument()
    expect(screen.getByText('5+ years')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View original posting' })).toHaveAttribute(
      'href',
      'https://boards.example.com/jobs/1',
    )
    expect(screen.getAllByText('Python').length).toBeGreaterThan(0)
  })

  it('renders the selected resume panel', async () => {
    renderDetail()

    await waitFor(() => expect(screen.getByText('Match')).toBeInTheDocument())
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
  })

  it('renders the profile_scores resume comparison panel (previously blocked, now unblocked)', async () => {
    renderDetail()

    // Wait on the "Selected" badge, which only renders once the
    // profile_scores-backed comparison list has actually loaded.
    expect(await screen.findByText('Selected')).toBeInTheDocument()
    expect(screen.getByText('Other resumes evaluated')).toBeInTheDocument()
    expect(screen.getAllByText('87%').length).toBeGreaterThan(0)
  })

  it('renders the application history timeline', async () => {
    renderDetail()

    expect(await screen.findByText('History')).toBeInTheDocument()
    expect(await screen.findByText('Triggered by system')).toBeInTheDocument()
  })

  it('wires ContactsPanel with the locked { jobId } contract', async () => {
    renderDetail()

    expect(await screen.findByTestId('contacts-panel-stub')).toHaveTextContent('Contacts for job-1')
  })

  it('performs a valid manual status update', async () => {
    let currentStatus = 'SHORTLISTED'
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
        HttpResponse.json(applicationFixture({ status: currentStatus })),
      ),
      http.patch(`${API_BASE_URL}/applications/:applicationId/status`, () => {
        currentStatus = 'APPLIED'
        return HttpResponse.json(applicationFixture({ status: currentStatus }))
      }),
    )
    renderDetail()
    const user = userEvent.setup()

    await waitFor(() => expect(screen.getByText('Update status')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Update' }))

    await waitFor(() => expect(screen.getAllByText('Applied').length).toBeGreaterThan(0))
  })

  it('shows a 409 rejection message and refetches current state instead of forcing the change locally', async () => {
    let attempts = 0
    server.use(
      http.patch(`${API_BASE_URL}/applications/:applicationId/status`, () => {
        attempts += 1
        return HttpResponse.json(
          { detail: { code: 'CONFLICT', message: 'This opportunity was already updated elsewhere' } },
          { status: 409 },
        )
      }),
    )
    renderDetail()
    const user = userEvent.setup()

    await waitFor(() => expect(screen.getByText('Update status')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Update' }))

    expect(await screen.findByText('This opportunity was already updated elsewhere')).toBeInTheDocument()
    expect(attempts).toBe(1)
    // Status shown is still whatever the (refetched) application query says
    // — never a locally-forced optimistic value.
    expect(screen.getAllByText('Shortlisted').length).toBeGreaterThan(0)
  })

  it('advances panels as the application progresses through polled statuses, stopping at the documented condition', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let call = 0
    const sequence = ['DISCOVERED', 'MATCHED', 'OUTREACH_SENT']
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () => {
        const status = sequence[Math.min(call, sequence.length - 1)]
        call += 1
        return HttpResponse.json(
          applicationFixture({ status, match_score: status === 'DISCOVERED' ? null : 0.87 }),
        )
      }),
    )
    renderDetail()

    await waitFor(() => expect(screen.getAllByText('Analyzing…').length).toBeGreaterThan(0))

    await vi.advanceTimersByTimeAsync(3100)
    await waitFor(() => expect(screen.getAllByText('Matched').length).toBeGreaterThan(0))

    await vi.advanceTimersByTimeAsync(3100)
    await waitFor(() => expect(screen.getAllByText('Outreach sent').length).toBeGreaterThan(0))

    const callsAtStop = call
    await vi.advanceTimersByTimeAsync(10000)
    expect(call).toBe(callsAtStop)

    vi.useRealTimers()
  })
})
