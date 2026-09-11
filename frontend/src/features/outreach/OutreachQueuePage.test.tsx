/**
 * See docs/frontend/routes.md#outreach--outreach-queue and
 * docs/frontend/agent-ownership.md's frontend-outreach-agent entry.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { IdentityContext } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { OutreachQueuePage } from './OutreachQueuePage'

function renderQueue() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityContext.Provider
        value={{ token: mintTestToken('user-1'), userId: 'user-1', setToken: vi.fn(), clearToken: vi.fn() }}
      >
        <OutreachQueuePage />
      </IdentityContext.Provider>
    </QueryClientProvider>,
  )
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

function mockStatusScopedList(byStatus: Record<string, unknown[]>) {
  server.use(
    http.get(`${API_BASE_URL}/outreach`, ({ request }) => {
      const url = new URL(request.url)
      const status = url.searchParams.get('status')
      if (status && status in byStatus) {
        return HttpResponse.json(byStatus[status])
      }
      if (!status && 'all' in byStatus) {
        return HttpResponse.json(byStatus.all)
      }
      return HttpResponse.json([])
    }),
  )
}

describe('OutreachQueuePage', () => {
  it('shows the pending-approval empty state when nothing needs review', async () => {
    mockStatusScopedList({ PENDING_APPROVAL: [], EDITED: [] })
    renderQueue()
    await waitFor(() => expect(screen.getByText("You're all caught up")).toBeInTheDocument())
  })

  it('renders PENDING_APPROVAL items with visually prominent "attention" status text', async () => {
    mockStatusScopedList({ PENDING_APPROVAL: [outreachFixture()], EDITED: [] })
    renderQueue()
    await waitFor(() => expect(screen.getAllByText('Needs your review').length).toBeGreaterThan(0))
    expect(screen.getByRole('tab', { name: /Needs review \(1\)/ })).toBeInTheDocument()
  })

  it('groups EDITED items into the same needs-review list as PENDING_APPROVAL (both still actionable)', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [outreachFixture({ id: 'outreach-1' })],
      EDITED: [outreachFixture({ id: 'outreach-2', status: 'EDITED', final_message: 'Edited draft' })],
    })
    renderQueue()
    await waitFor(() => expect(screen.getByRole('tab', { name: /Needs review \(2\)/ })).toBeInTheDocument())
    expect(screen.getAllByText(/Hi Jane, \.\.\./).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Edited draft/).length).toBeGreaterThan(0)
  })

  it('selecting a row renders the OutreachReviewPanel inline, with no navigation away from the queue', async () => {
    mockStatusScopedList({ PENDING_APPROVAL: [outreachFixture()], EDITED: [] })
    renderQueue()
    await waitFor(() => expect(screen.getAllByText(/Hi Jane/).length).toBeGreaterThan(0))

    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()

    const [row] = screen.getAllByRole('button', { name: /Hi Jane/i })
    await userEvent.click(row)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument())
  })

  it('there is no bulk "approve all" control anywhere on the page (no automatic-approval path)', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [outreachFixture({ id: 'outreach-1' }), outreachFixture({ id: 'outreach-2' })],
      EDITED: [],
    })
    renderQueue()
    await waitFor(() => expect(screen.getByRole('tab', { name: /Needs review \(2\)/ })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /approve all/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/approve all/i)).not.toBeInTheDocument()
    // No Approve control exists until a specific row is explicitly selected.
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('the History tab shows decided/terminal items (APPROVED/REJECTED/SENT/SEND_FAILED) and excludes pending ones', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [],
      EDITED: [],
      all: [
        outreachFixture({ id: 'outreach-approved', status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }),
        outreachFixture({
          id: 'outreach-sent',
          status: 'SENT',
          decided_at: '2026-01-02T00:00:00Z',
          sent_at: '2026-01-03T00:00:00Z',
          draft_message: 'Hi again',
        }),
        outreachFixture({
          id: 'outreach-failed',
          status: 'SEND_FAILED',
          decided_at: '2026-01-02T00:00:00Z',
          draft_message: 'Hi failed',
        }),
        outreachFixture({ id: 'outreach-pending-should-be-excluded', status: 'PENDING_APPROVAL' }),
      ],
    })
    renderQueue()
    await userEvent.click(screen.getByRole('tab', { name: 'History' }))

    await waitFor(() => expect(screen.getAllByText('Approved').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Sent').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Send failed').length).toBeGreaterThan(0)
  })

  it('selecting a SENT item in History shows "Sent." copy; selecting an APPROVED item never shows "Sent."', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [],
      EDITED: [],
      all: [
        outreachFixture({ id: 'outreach-approved', status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }),
        outreachFixture({
          id: 'outreach-sent',
          status: 'SENT',
          decided_at: '2026-01-02T00:00:00Z',
          sent_at: '2026-01-03T00:00:00Z',
          draft_message: 'Hi again',
        }),
      ],
    })
    renderQueue()
    await userEvent.click(screen.getByRole('tab', { name: 'History' }))
    await waitFor(() => expect(screen.getAllByText('Approved').length).toBeGreaterThan(0))

    const approvedRows = screen.getAllByRole('button', { name: /Hi Jane/i })
    await userEvent.click(approvedRows[0])
    await waitFor(() => expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument())
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()

    const sentRows = screen.getAllByRole('button', { name: /Hi again/i })
    await userEvent.click(sentRows[0])
    await waitFor(() => expect(screen.getByText('Sent.', { exact: true })).toBeInTheDocument())
  })

  it('shows a page-level error state with retry when the queue fails to load', async () => {
    server.use(http.get(`${API_BASE_URL}/outreach`, () => HttpResponse.error()))
    renderQueue()
    await waitFor(() => expect(screen.getByText("Couldn't load the outreach queue.")).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('renders non-software-engineering draft content in the list with no hard-coded assumptions', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [
        outreachFixture({
          id: 'outreach-hr',
          draft_message: 'Hello, I am following up regarding the Talent Acquisition Partner role at Meridian HR.',
        }),
      ],
      EDITED: [],
    })
    renderQueue()
    // The list truncates long previews (72 chars) — assert on a prefix
    // that survives truncation rather than the full string.
    await waitFor(() =>
      expect(screen.getAllByText(/Talent Acquisition Partner/).length).toBeGreaterThan(0),
    )
  })

  it('distinguishes an approved row from a sent row in the list itself, not just inside the panel', async () => {
    mockStatusScopedList({
      PENDING_APPROVAL: [],
      EDITED: [],
      all: [
        outreachFixture({ id: 'outreach-approved', status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }),
        outreachFixture({
          id: 'outreach-sent',
          status: 'SENT',
          decided_at: '2026-01-02T00:00:00Z',
          sent_at: '2026-01-03T00:00:00Z',
          draft_message: 'Hi again',
        }),
      ],
    })
    renderQueue()
    await userEvent.click(screen.getByRole('tab', { name: 'History' }))

    // Both statuses share the "positive" badge category, so the delivery
    // line under the badge is what keeps them from reading identically.
    await waitFor(() => expect(screen.getAllByText('Awaiting send').length).toBeGreaterThan(0))
    expect(screen.getAllByText(/^Delivered /).length).toBeGreaterThan(0)
    // An approved row is never described as delivered/sent.
    expect(screen.getAllByText('Approved').length).toBeGreaterThan(0)
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()
  })

  it('editing a draft from the queue moves it to EDITED and that shows in the list, not just the panel', async () => {
    let edited = false
    server.use(
      http.get(`${API_BASE_URL}/outreach`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        const record = outreachFixture({
          status: edited ? 'EDITED' : 'PENDING_APPROVAL',
          final_message: edited ? 'Rewritten by the reviewer' : null,
        })
        if (status === 'PENDING_APPROVAL') return HttpResponse.json(edited ? [] : [record])
        if (status === 'EDITED') return HttpResponse.json(edited ? [record] : [])
        return HttpResponse.json([])
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () => {
        edited = true
        return HttpResponse.json(
          outreachFixture({ status: 'EDITED', final_message: 'Rewritten by the reviewer' }),
        )
      }),
    )
    renderQueue()
    const [row] = await screen.findAllByRole('button', { name: /Hi Jane/i })
    await userEvent.click(row)

    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    const textarea = screen.getByRole('textbox')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'Rewritten by the reviewer')
    await userEvent.click(screen.getByRole('button', { name: 'Save edit' }))

    // The list row now carries the EDITED badge and the edited text...
    await waitFor(() => expect(screen.getAllByText('Edited, needs your review').length).toBeGreaterThan(0))
    expect(screen.getAllByText(/Rewritten by the reviewer/).length).toBeGreaterThan(0)
    // ...and the item is still actionable, since EDITED is not a decision.
    expect(screen.getByText('Edited — still needs your approval or rejection.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  })

  it('a 409 while approving from the queue shows the "already decided" state and refetches, instead of failing silently or retrying', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error').mockImplementation(() => '')
    let decidedElsewhere = false
    let approveCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        const record = outreachFixture({
          status: decidedElsewhere ? 'APPROVED' : 'PENDING_APPROVAL',
          decided_at: decidedElsewhere ? '2026-01-02T00:00:00Z' : null,
        })
        if (status === 'PENDING_APPROVAL') return HttpResponse.json(decidedElsewhere ? [] : [record])
        if (status === 'EDITED') return HttpResponse.json([])
        // Unfiltered (History) — the decided record belongs here now.
        return HttpResponse.json(decidedElsewhere ? [record] : [])
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () => {
        // Another tab got there first.
        approveCalls += 1
        decidedElsewhere = true
        return HttpResponse.json(
          { detail: { code: 'CONFLICT', message: 'This was already decided' } },
          { status: 409 },
        )
      }),
    )
    renderQueue()
    const [row] = await screen.findAllByRole('button', { name: /Hi Jane/i })
    await userEvent.click(row)
    await userEvent.click(await screen.findByRole('button', { name: 'Approve' }))

    // Specific inline message, never a toast, and the panel survives the
    // row leaving the "Needs review" list.
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))
    expect(toastErrorSpy).not.toHaveBeenCalled()

    // The refetch triggered by the 409 replaces the stale record with the
    // real current state — approved by someone else, and no longer actionable.
    await waitFor(() => expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    // Never re-sent automatically.
    expect(approveCalls).toBe(1)
    toastErrorSpy.mockRestore()
  })

  it('the reading pane never appears without an explicit row selection (no auto-selection on load)', async () => {
    mockStatusScopedList({ PENDING_APPROVAL: [outreachFixture()], EDITED: [] })
    renderQueue()
    await waitFor(() => expect(screen.getAllByText(/Hi Jane/).length).toBeGreaterThan(0))
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })
})
