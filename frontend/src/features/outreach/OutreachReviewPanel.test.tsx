/**
 * See docs/frontend/user-flows.md#outreach-review-ux,
 * docs/frontend/error-handling.md's 409 row, and this component's own doc
 * comment for the approve-vs-edit UX decision (separate calls, not
 * combined).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import type { OutreachResponse } from '../../api/types'
import { OutreachReviewPanel } from './OutreachReviewPanel'

function makeOutreach(overrides: Partial<OutreachResponse> = {}): OutreachResponse {
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

function renderPanel(outreach: OutreachResponse, applicationId?: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <OutreachReviewPanel outreach={outreach} applicationId={applicationId} />
    </QueryClientProvider>,
  )
}

describe('OutreachReviewPanel', () => {
  it('renders the draft message, channel, recipient, and status for a pending item', async () => {
    renderPanel(makeOutreach())

    expect(screen.getByText('Hi Jane, ...')).toBeInTheDocument()
    expect(screen.getByText(/Channel: Email/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/Jane Doe/)).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/Senior Backend Engineer.*Acme Robotics/)).toBeInTheDocument())
    expect(screen.getByText('Needs your review')).toBeInTheDocument()
  })

  it('shows approve/edit/reject actions for PENDING_APPROVAL and EDITED, but not for terminal statuses', () => {
    const { unmount } = renderPanel(makeOutreach({ status: 'PENDING_APPROVAL' }))
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    unmount()

    const { unmount: unmount2 } = renderPanel(makeOutreach({ status: 'EDITED', final_message: 'Edited' }))
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    unmount2()

    renderPanel(makeOutreach({ status: 'REJECTED', decided_at: '2026-01-02T00:00:00Z' }))
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('never calls approve, edit, or reject automatically — every mutation requires a direct click (no auto-approval path)', async () => {
    let approveCalls = 0
    let editCalls = 0
    let rejectCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () => {
        approveCalls += 1
        return HttpResponse.json(makeOutreach({ status: 'APPROVED' }))
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () => {
        editCalls += 1
        return HttpResponse.json(makeOutreach({ status: 'EDITED' }))
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/reject`, () => {
        rejectCalls += 1
        return HttpResponse.json(makeOutreach({ status: 'REJECTED' }))
      }),
    )
    renderPanel(makeOutreach())
    await waitFor(() => expect(screen.getByText(/Jane Doe/)).toBeInTheDocument())
    // Give any (incorrect) mount-triggered mutation a chance to fire.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(approveCalls).toBe(0)
    expect(editCalls).toBe(0)
    expect(rejectCalls).toBe(0)
    // And there is no bulk/"approve all"/auto affordance anywhere in the panel.
    expect(screen.queryByText(/approve all/i)).not.toBeInTheDocument()
  })

  it('Approve calls the approve endpoint with final_message: null', async () => {
    let capturedBody: unknown = null
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json(makeOutreach({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }))
      }),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(capturedBody).toEqual({ final_message: null }))
  })

  it('Edit reveals a pre-filled form; Save edit calls the edit endpoint (a separate call from Approve)', async () => {
    let editBody: unknown = null
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, async ({ request }) => {
        editBody = await request.json()
        return HttpResponse.json(makeOutreach({ status: 'EDITED', final_message: 'Updated message' }))
      }),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: /Edit/ }))

    const textarea = screen.getByRole('textbox')
    expect(textarea).toHaveValue('Hi Jane, ...')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'Updated message')
    await userEvent.click(screen.getByRole('button', { name: 'Save edit' }))

    await waitFor(() => expect(editBody).toEqual({ message: 'Updated message' }))
    // The edit form closes back to the read view on success. (This panel
    // is purely prop-driven — it does not locally splice the mutation's
    // response into what it displays; the calling page's active query
    // refetches and passes the real updated record down. See
    // OutreachReviewPage.test.tsx for that full refetch-after-edit flow.)
    await waitFor(() => expect(screen.queryByRole('textbox')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Save edit' })).not.toBeInTheDocument()
  })

  it('renders the EDITED state as still-actionable, with its own helper copy', () => {
    renderPanel(makeOutreach({ status: 'EDITED', final_message: 'Edited draft text' }))
    expect(screen.getByText('Edited draft text')).toBeInTheDocument()
    expect(screen.getByText('Edited — still needs your approval or rejection.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('Reject requires confirming a dialog before calling the reject endpoint', async () => {
    let rejectCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/reject`, () => {
        rejectCalls += 1
        return HttpResponse.json(makeOutreach({ status: 'REJECTED', decided_at: '2026-01-02T00:00:00Z' }))
      }),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    // Clicking the top-level Reject only opens the confirmation dialog —
    // the endpoint is not called yet.
    expect(rejectCalls).toBe(0)

    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(rejectCalls).toBe(1))
  })

  it('on a 409 from approve, shows the specific "already decided" message inline (not a generic toast) and reflects refetched server state, not local optimistic state', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error')
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))
    expect(toastErrorSpy).not.toHaveBeenCalled()
    // Still showing the PENDING_APPROVAL badge passed in via props — the
    // panel never assumed local "approved" state from the failed call.
    expect(screen.getByText('Needs your review')).toBeInTheDocument()
    expect(screen.queryByText('Approved — will be sent shortly.')).not.toBeInTheDocument()
    toastErrorSpy.mockRestore()
  })

  it('on a 409 from reject, shows the conflict message inline', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/reject`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))
  })

  it('on a 409 from edit, shows the conflict message inline', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: /Edit/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save edit' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))
  })

  it('Step 12 regression: a 409 conflict message survives the built-in refetch that resolves it — it must not vanish just because outreach.status/decided_at changed to reflect the real current state', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <OutreachReviewPanel outreach={makeOutreach()} />
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))

    // Simulate the built-in 409-refetch landing with the real current
    // state — same outreach.id, but status/decided_at now reflect that
    // someone else already approved it. Before the Step 12 fix, this
    // exact prop change cleared the banner via an effect keyed on
    // [outreach.status, outreach.decided_at], erasing it within the same
    // tick it appeared.
    rerender(
      <QueryClientProvider client={queryClient}>
        <OutreachReviewPanel
          outreach={makeOutreach({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' })}
        />
      </QueryClientProvider>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('This was already decided')
    expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument()
  })

  it('Step 12 regression: a conflict message clears when the panel switches to a different outreach item', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <OutreachReviewPanel outreach={makeOutreach({ id: 'outreach-1' })} />
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    rerender(
      <QueryClientProvider client={queryClient}>
        <OutreachReviewPanel outreach={makeOutreach({ id: 'outreach-2' })} />
      </QueryClientProvider>,
    )

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('Step 12 regression: a conflict message clears the moment the user retries the action, and does not linger after a subsequent successful attempt', async () => {
    let approveCalls = 0
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () => {
        approveCalls += 1
        if (approveCalls === 1) {
          return HttpResponse.json(
            { detail: { code: 'CONFLICT', message: 'This was already decided' } },
            { status: 409 },
          )
        }
        return HttpResponse.json(makeOutreach({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }))
      }),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(approveCalls).toBe(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('a non-409 mutation failure (e.g. validation) shows a toast, not the inline conflict banner', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error').mockImplementation(() => '')
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'message is required' } }, { status: 400 }),
      ),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: /Edit/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save edit' }))
    await waitFor(() => expect(toastErrorSpy).toHaveBeenCalledWith('message is required'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    toastErrorSpy.mockRestore()
  })

  it('Generated != Sent: the panel never renders "Sent" copy off the back of a raw approve-mutation response — only the `outreach` prop drives status copy', async () => {
    // The mutation resolves with `status: 'APPROVED'` (as the real approve
    // endpoint always does — it can never legitimately return `SENT`, see
    // state-machines.md). This panel deliberately never reads
    // `approveMutation.data` to render status copy — only the `outreach`
    // prop, which is owned by the calling page and only updates once a
    // real `GET` refetch confirms the current server state (see
    // OutreachReviewPage.test.tsx for that full, page-level flow). So even
    // after this mutation resolves successfully, the static prop passed
    // into this render (still `PENDING_APPROVAL`) is what's shown — proving
    // there is no local/optimistic "approved" or "sent" shortcut anywhere
    // in this component.
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json(makeOutreach({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' })),
      ),
    )
    renderPanel(makeOutreach())
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).not.toBeDisabled())
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Sent', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Approved — will be sent shortly.')).not.toBeInTheDocument()
  })

  it('renders "Approved — will be sent shortly." for a fetched APPROVED record — never "Sent"', () => {
    renderPanel(makeOutreach({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' }))
    expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument()
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Sent', { exact: true })).not.toBeInTheDocument()
  })

  it('renders "Sent." only once the fetched record itself has status SENT', () => {
    renderPanel(makeOutreach({ status: 'SENT', sent_at: '2026-01-03T00:00:00Z', decided_at: '2026-01-02T00:00:00Z' }))
    expect(screen.getByText('Sent.', { exact: true })).toBeInTheDocument()
    expect(screen.getAllByText('Sent', { exact: true }).length).toBeGreaterThan(0) // the StatusBadge itself
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('renders a distinct SEND_FAILED state', () => {
    renderPanel(makeOutreach({ status: 'SEND_FAILED', decided_at: '2026-01-02T00:00:00Z' }))
    expect(screen.getByText('Send failed')).toBeInTheDocument()
    expect(screen.getByText(/Sending failed\. This requires manual follow-up/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('renders a rejected (terminal) state with no further actions', () => {
    renderPanel(makeOutreach({ status: 'REJECTED', decided_at: '2026-01-02T00:00:00Z' }))
    expect(screen.getByText('Rejected.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument()
  })

  it('renders non-software-engineering draft content with no hard-coded profession assumptions', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId`, () =>
        HttpResponse.json({
          id: 'job-9',
          user_id: 'user-1',
          company: 'Titan Aerospace',
          title: 'Mechanical Design Engineer',
          location: 'Seattle, WA',
          description: 'Design structural components.',
          extracted_skills: ['CAD', 'GD&T'],
          experience_required: '3+ years',
          source_url: 'https://boards.example.com/jobs/9',
          processing_status: 'NORMALIZED',
          discovered_at: '2026-01-01T00:00:00Z',
        }),
      ),
      http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () =>
        HttpResponse.json([
          {
            id: 'contact-9',
            full_name: 'Priya Raman',
            headline: 'Senior Mechanical Design Lead',
            company: 'Titan Aerospace',
            contact_type: 'HIRING_MANAGER',
            profile_url: 'https://example.com/priya',
            relevance_score: 0.88,
            status: 'RANKED',
          },
        ]),
      ),
    )
    renderPanel(
      makeOutreach({
        job_id: 'job-9',
        contact_id: 'contact-9',
        draft_message:
          'Hi Priya, I noticed the Mechanical Design Engineer opening at Titan Aerospace and would love to learn more about the structural team.',
      }),
    )
    expect(
      screen.getByText(
        'Hi Priya, I noticed the Mechanical Design Engineer opening at Titan Aerospace and would love to learn more about the structural team.',
      ),
    ).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/Priya Raman/)).toBeInTheDocument())
    await waitFor(() =>
      expect(screen.getByText(/Target:\s*Mechanical Design Engineer · Titan Aerospace/)).toBeInTheDocument(),
    )
  })
})
