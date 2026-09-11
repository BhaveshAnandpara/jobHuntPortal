/**
 * Route: /opportunities/:applicationId — see
 * docs/frontend/routes.md#opportunitiesapplicationid--opportunity-detail.
 *
 * Covers: full `JobResponse` rendering (Step 10.5 fields), the selected
 * resume panel, the `profile_scores` resume-comparison panel (previously
 * blocked, now unblocked — a core scenario, not skipped), the lifecycle
 * timeline, a valid manual status update, a rejected transition showing the
 * error and refetching (not forcing state locally), the wired `ContactsPanel`
 * integration, and processing polling advancing panels across statuses until
 * the poll's documented stop condition.
 *
 * T8 (docs/frontend/frontend-revamp-spec.md) adds the two acceptance criteria
 * that are specific to this page:
 *
 * - **"the correct set of populated vs. placeholder panels" at each major
 *   lifecycle stage** — one case per stage, asserting both what unlocked and
 *   what is still locked, plus the two cases `ApplicationStatus` alone can't
 *   answer (a manual jump forward, and a manual jump with history proving the
 *   chain did run).
 * - **"a failed secondary panel doesn't block the rest of the page"** — this
 *   is asserted by failing one secondary query at a time and checking the
 *   other panels still render their real content. `ContactsPanel` is used for
 *   real here (not stubbed) precisely so its failure is a real failure; a
 *   stub could not demonstrate isolation.
 */

import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { OpportunityDetailPage } from './OpportunityDetailPage'
import type { ApplicationStatus } from '../../api/types'

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

function historyFixture(toStatus: string, fromStatus: string | null = null, index = 0) {
  return {
    id: `hist-${index}-${toStatus}`,
    application_id: 'app-1',
    from_status: fromStatus,
    to_status: toStatus,
    changed_at: `2026-01-0${index + 1}T00:00:00Z`,
    triggered_by: 'system',
    source_event_type: 'JOB_DISCOVERED',
    correlation_id: null,
  }
}

function outreachFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'outreach-1',
    job_id: 'job-1',
    contact_id: 'contact-1',
    channel: 'EMAIL',
    draft_message: 'Hi Jane, ...',
    final_message: null,
    status: 'PENDING_APPROVAL',
    generated_at: '2026-01-01T00:00:00Z',
    decided_at: null,
    sent_at: null,
    ...overrides,
  }
}

/**
 * Puts the application at a given status with a history that matches it, and
 * keeps the rest of the fixtures consistent with that chain: an opportunity
 * that never reached `OUTREACH_GENERATED` has no outreach record, so the
 * default handler's draft is cleared unless the chain says otherwise. Tests
 * that need a specific outreach record override `GET /outreach` themselves,
 * after calling this.
 */
function atStatus(status: ApplicationStatus, chain: string[]) {
  server.use(
    http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
      HttpResponse.json(
        applicationFixture({ status, match_score: chain.includes('MATCHED') ? 0.87 : null }),
      ),
    ),
    http.get(`${API_BASE_URL}/applications/:applicationId/history`, () =>
      HttpResponse.json(chain.map((entry, index) => historyFixture(entry, chain[index - 1] ?? null, index))),
    ),
    http.get(`${API_BASE_URL}/outreach`, () =>
      HttpResponse.json(chain.includes('OUTREACH_GENERATED') ? [outreachFixture()] : []),
    ),
  )
}

function renderDetail(applicationId = 'app-1') {
  setToken(mintTestToken('user-1'))
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

/** The panel `<section>`-equivalent for a heading, so assertions stay panel-scoped. */
function panel(heading: string): HTMLElement {
  const title = screen.getByText(heading)
  const card = title.closest('div[data-slot="card"]') ?? title.parentElement?.parentElement
  if (!card) {
    throw new Error(`Could not find the panel containing "${heading}"`)
  }
  return card as HTMLElement
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

    // Labeled by the uploaded file name (`resume.pdf`, from `GET
    // /resumes`), not the extracted `title` — several resumes from the
    // same candidate often extract to the same title, making them
    // indistinguishable by title alone.
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
  })

  it('renders the application history timeline', async () => {
    renderDetail()

    expect(await screen.findByText('History')).toBeInTheDocument()
    // `triggered_by: "system"` is a backend component name, shown as what it
    // means rather than printed raw.
    expect(await screen.findByText(/Changed automatically/)).toBeInTheDocument()
  })

  it('wires ContactsPanel against the job id from the application', async () => {
    atStatus('CONTACT_FOUND', ['DISCOVERED', 'MATCHED', 'SHORTLISTED', 'CONTACT_SEARCH', 'CONTACT_FOUND'])
    renderDetail()

    // `GET /jobs/job-1/contacts`'s default handler returns Jane Doe — proof
    // the panel was handed this application's `job_id`, not the route param.
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
  })
})

describe('OpportunityDetailPage — progressive panel unlocking', () => {
  it('locks match, contacts and outreach for a freshly discovered opportunity', async () => {
    atStatus('DISCOVERED', ['DISCOVERED'])
    renderDetail()

    expect(await screen.findByText('Not matched yet')).toBeInTheDocument()
    expect(screen.getByText('No comparison yet')).toBeInTheDocument()
    expect(screen.getByText("Contact search hasn't started")).toBeInTheDocument()
    expect(screen.getByText('No draft yet')).toBeInTheDocument()

    // A placeholder is not a spinner: nothing on the page should be claiming
    // these panels are loading.
    expect(screen.queryByLabelText('Loading contacts')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Loading resume comparison')).not.toBeInTheDocument()
  })

  it('unlocks match at MATCHED while contacts and outreach stay locked', async () => {
    atStatus('MATCHED', ['DISCOVERED', 'MATCHED'])
    renderDetail()

    // Real match data, not a placeholder.
    await waitFor(() => expect(within(panel('Match')).getByText('87%')).toBeInTheDocument())
    expect(screen.queryByText('Not matched yet')).not.toBeInTheDocument()
    expect(within(panel('Match')).getByText('Python')).toBeInTheDocument()
    expect(within(panel('Match')).getByText('Kafka')).toBeInTheDocument()

    expect(screen.getByText("Contact search hasn't started")).toBeInTheDocument()
    expect(screen.getByText('No draft yet')).toBeInTheDocument()
  })

  it('unlocks contacts at CONTACT_SEARCH while outreach stays locked', async () => {
    atStatus('CONTACT_SEARCH', ['DISCOVERED', 'MATCHED', 'SHORTLISTED', 'CONTACT_SEARCH'])
    renderDetail()

    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    expect(screen.queryByText("Contact search hasn't started")).not.toBeInTheDocument()
    expect(screen.getByText('No draft yet')).toBeInTheDocument()
  })

  it('unlocks outreach at OUTREACH_GENERATED', async () => {
    atStatus('OUTREACH_GENERATED', [
      'DISCOVERED',
      'MATCHED',
      'SHORTLISTED',
      'CONTACT_SEARCH',
      'CONTACT_FOUND',
      'OUTREACH_GENERATED',
    ])
    renderDetail()

    expect(await screen.findByRole('link', { name: 'Review' })).toHaveAttribute(
      'href',
      '/outreach/outreach-1',
    )
    expect(screen.queryByText('No draft yet')).not.toBeInTheDocument()
  })

  it('keeps stages unlocked after a manual jump, reading history rather than the current status', async () => {
    // APPLIED is reachable manually from almost anywhere, so the status alone
    // says nothing about how far the automated chain got. History does.
    atStatus('APPLIED', ['DISCOVERED', 'MATCHED', 'SHORTLISTED', 'CONTACT_SEARCH', 'CONTACT_FOUND'])
    renderDetail()

    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    expect(screen.queryByText("Contact search hasn't started")).not.toBeInTheDocument()
    expect(screen.queryByText('Not matched yet')).not.toBeInTheDocument()
    // Outreach genuinely never ran on this one — still locked.
    expect(screen.getByText('No draft yet')).toBeInTheDocument()
  })

  it('locks contacts for an opportunity ignored straight out of matching', async () => {
    atStatus('IGNORED', ['DISCOVERED', 'MATCHED', 'IGNORED'])
    renderDetail()

    expect(await screen.findByText("Contact search hasn't started")).toBeInTheDocument()
    // Matching did run before it was ignored, so that panel stays populated.
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
  })
})

describe('OpportunityDetailPage — independent panel errors', () => {
  it('keeps every other panel usable when the contacts query fails', async () => {
    atStatus('CONTACT_FOUND', ['DISCOVERED', 'MATCHED', 'SHORTLISTED', 'CONTACT_SEARCH', 'CONTACT_FOUND'])
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'Contacts service unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderDetail()

    expect(await screen.findByText('Contacts service unavailable.')).toBeInTheDocument()

    // Everything else still rendered its real content.
    expect(await screen.findByText('Own our platform services.')).toBeInTheDocument()
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
    expect(screen.getByText('History')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Update' })).toBeEnabled()
  })

  it('keeps every other panel usable when the job query fails', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'Job service unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderDetail()

    expect(await screen.findByText('Job service unavailable.')).toBeInTheDocument()
    // The page header comes from the application, which loaded fine.
    expect(screen.getByRole('heading', { name: 'Senior Backend Engineer' })).toBeInTheDocument()
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
    expect(screen.getByText('History')).toBeInTheDocument()
  })

  it('keeps every other panel usable when the outreach query fails', async () => {
    atStatus('OUTREACH_GENERATED', [
      'DISCOVERED',
      'MATCHED',
      'SHORTLISTED',
      'CONTACT_SEARCH',
      'CONTACT_FOUND',
      'OUTREACH_GENERATED',
    ])
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'Outreach service unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderDetail()

    expect(await screen.findByText('Outreach service unavailable.')).toBeInTheDocument()
    expect(await screen.findByText('Own our platform services.')).toBeInTheDocument()
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
  })

  it('keeps every other panel usable when the history query fails, and does not re-lock panels', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
        HttpResponse.json(applicationFixture({ status: 'CONTACT_FOUND' })),
      ),
      http.get(`${API_BASE_URL}/applications/:applicationId/history`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'History unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderDetail()

    expect(await screen.findByText('History unavailable.')).toBeInTheDocument()
    // History is one of three evidence sources for unlocking; losing it must
    // degrade to status + match_score, never to "this stage hasn't started".
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    expect(screen.queryByText("Contact search hasn't started")).not.toBeInTheDocument()
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
  })

  it('keeps the match score and skills when only the matches query fails', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/matches`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'Matching service unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderDetail()

    // Only the recommendation and the per-resume comparison come from that
    // query; score and skills come from the already-loaded application.
    await waitFor(() =>
      expect(screen.getAllByText('Matching service unavailable.').length).toBeGreaterThan(0),
    )
    expect(within(panel('Match')).getByText('87%')).toBeInTheDocument()
    expect(within(panel('Match')).getByText('Python')).toBeInTheDocument()
    expect(within(panel('Match')).getByText('Kafka')).toBeInTheDocument()
  })

  it('shows a page-level error only when the primary application query fails', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Nope' } }, { status: 404 }),
      ),
    )
    renderDetail()

    expect(await screen.findByText('This opportunity could not be found.')).toBeInTheDocument()
    // Nothing else is rendered — there is no page without its primary payload.
    expect(screen.queryByText('Job information')).not.toBeInTheDocument()
    expect(screen.queryByText('Update status')).not.toBeInTheDocument()
  })
})

describe('OpportunityDetailPage — empty states are distinct from placeholders', () => {
  it('shows the contacts empty state (not an error) for a valid 200 empty list', async () => {
    atStatus('CONTACT_FOUND', ['DISCOVERED', 'MATCHED', 'SHORTLISTED', 'CONTACT_SEARCH', 'CONTACT_FOUND'])
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => HttpResponse.json([])))
    renderDetail()

    expect(
      await screen.findByText('No relevant contacts found for this company yet'),
    ).toBeInTheDocument()
    // "Ran and found nobody" must not read as "hasn't run", and must not be an error.
    expect(screen.queryByText("Contact search hasn't started")).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows an outreach empty state when generation ran but no draft exists for this job', async () => {
    atStatus('OUTREACH_GENERATED', [
      'DISCOVERED',
      'MATCHED',
      'SHORTLISTED',
      'CONTACT_SEARCH',
      'CONTACT_FOUND',
      'OUTREACH_GENERATED',
    ])
    // A populated list that contains nothing for *this* job — the client-side
    // `job_id` filter is what has to produce the empty result.
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () =>
        HttpResponse.json([outreachFixture({ id: 'other', job_id: 'job-999' })]),
      ),
    )
    renderDetail()

    expect(
      await screen.findByText('No outreach draft exists for this opportunity.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('No draft yet')).not.toBeInTheDocument()
  })
})

describe('OpportunityDetailPage — Approved and Sent stay distinct (spec Section 2.6)', () => {
  it('describes an approved draft as awaiting send, never as sent', async () => {
    atStatus('OUTREACH_APPROVED', [
      'DISCOVERED',
      'MATCHED',
      'SHORTLISTED',
      'CONTACT_SEARCH',
      'CONTACT_FOUND',
      'OUTREACH_GENERATED',
      'OUTREACH_APPROVED',
    ])
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () =>
        HttpResponse.json([
          outreachFixture({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }),
        ]),
      ),
    )
    renderDetail()

    // Wording comes from T9's shared `outreachCopy.ts`, so this panel cannot
    // drift from the /outreach surface.
    expect(await screen.findByText('Awaiting send')).toBeInTheDocument()
    expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument()
    expect(within(panel('Outreach')).queryByText('Sent.')).not.toBeInTheDocument()
  })

  it('describes a sent message as delivered, with different wording from approved', async () => {
    atStatus('OUTREACH_SENT', [
      'DISCOVERED',
      'MATCHED',
      'SHORTLISTED',
      'CONTACT_SEARCH',
      'CONTACT_FOUND',
      'OUTREACH_GENERATED',
      'OUTREACH_APPROVED',
      'OUTREACH_SENT',
    ])
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () =>
        HttpResponse.json([
          outreachFixture({
            status: 'SENT',
            decided_at: '2026-01-02T00:00:00Z',
            sent_at: '2026-01-03T00:00:00Z',
          }),
        ]),
      ),
    )
    renderDetail()

    expect(await screen.findByText(/^Delivered /)).toBeInTheDocument()
    expect(screen.getByText('Sent.')).toBeInTheDocument()
    expect(screen.queryByText('Awaiting send')).not.toBeInTheDocument()
    expect(screen.queryByText('Approved — will be sent shortly.')).not.toBeInTheDocument()
  })
})

describe('OpportunityDetailPage — status action menu', () => {
  it('performs a valid manual status update and confirms it', async () => {
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
    // Post-action confirmation, named from the server's response.
    expect(await screen.findByText('Status updated to Applied.')).toBeInTheDocument()
  })

  it('shows a 400 VALIDATION_ERROR inline and refetches current state instead of forcing the change locally', async () => {
    // This endpoint rejects an invalid transition as a 400 VALIDATION_ERROR —
    // 409 belongs to the outreach approve/reject endpoints, which this menu
    // never calls (error-handling.md#invalid-state-transition-ux).
    let attempts = 0
    let applicationFetches = 0
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () => {
        applicationFetches += 1
        return HttpResponse.json(applicationFixture({ status: 'SHORTLISTED' }))
      }),
      http.patch(`${API_BASE_URL}/applications/:applicationId/status`, () => {
        attempts += 1
        return HttpResponse.json(
          {
            detail: {
              code: 'VALIDATION_ERROR',
              message: 'Cannot transition from SHORTLISTED to APPLIED',
            },
          },
          { status: 400 },
        )
      }),
    )
    renderDetail()
    const user = userEvent.setup()

    await waitFor(() => expect(screen.getByText('Update status')).toBeInTheDocument())
    const fetchesBefore = applicationFetches
    await user.click(screen.getByRole('button', { name: 'Update' }))

    // The server's own message, announced, not swallowed.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Cannot transition from SHORTLISTED to APPLIED')

    // Rejected once, never retried blindly...
    expect(attempts).toBe(1)
    // ...and current state was refetched rather than assumed.
    await waitFor(() => expect(applicationFetches).toBeGreaterThan(fetchesBefore))
    // Status shown is still whatever the (refetched) application query says
    // — never a locally-forced optimistic value.
    expect(screen.getAllByText('Shortlisted').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Status updated to/)).not.toBeInTheDocument()
  })

  it('offers no transitions from a terminal status, and says which one it is in', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
        HttpResponse.json(applicationFixture({ status: 'OFFER' })),
      ),
    )
    renderDetail()

    expect(
      await screen.findByText(/This opportunity is marked Offer\. There are no further status changes/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument()
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
      http.get(`${API_BASE_URL}/applications/:applicationId/history`, () => HttpResponse.json([])),
    )
    renderDetail()

    await waitFor(() => expect(screen.getAllByText('Analyzing…').length).toBeGreaterThan(0))
    // At DISCOVERED the match panel is a placeholder, not a score.
    expect(screen.getByText('Not matched yet')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(3100)
    await waitFor(() => expect(screen.getAllByText('Matched').length).toBeGreaterThan(0))
    // The match panel unlocked without a page reload.
    await waitFor(() => expect(within(panel('Match')).getByText('87%')).toBeInTheDocument())
    expect(screen.queryByText('Not matched yet')).not.toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(3100)
    await waitFor(() => expect(screen.getAllByText('Outreach sent').length).toBeGreaterThan(0))

    const callsAtStop = call
    await vi.advanceTimersByTimeAsync(10000)
    expect(call).toBe(callsAtStop)

    vi.useRealTimers()
  })
})
