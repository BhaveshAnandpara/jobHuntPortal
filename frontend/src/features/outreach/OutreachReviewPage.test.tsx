/**
 * See docs/frontend/routes.md#outreachoutreachid--outreach-review-deep-link.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { IdentityContext } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { OutreachReviewPage } from './OutreachReviewPage'

function renderDeepLink(outreachId = 'outreach-1') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityContext.Provider
        value={{ token: mintTestToken('user-1'), userId: 'user-1', setToken: vi.fn(), clearToken: vi.fn() }}
      >
        <MemoryRouter initialEntries={[`/outreach/${outreachId}`]}>
          <Routes>
            <Route path="/outreach/:outreachId" element={<OutreachReviewPage />} />
            <Route path="/outreach" element={<div>Back at the queue</div>} />
          </Routes>
        </MemoryRouter>
      </IdentityContext.Provider>
    </QueryClientProvider>,
  )
}

describe('OutreachReviewPage (deep link)', () => {
  it('fetches GET /outreach/{id} directly and renders the shared OutreachReviewPanel', async () => {
    let requestedPath: string | undefined
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, ({ request, params }) => {
        requestedPath = request.url
        return HttpResponse.json({
          id: params.outreachId,
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'LINKEDIN_MESSAGE',
          draft_message: 'Hi Jane, deep-linked draft.',
          final_message: null,
          status: 'PENDING_APPROVAL',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: null,
          sent_at: null,
        })
      }),
    )
    renderDeepLink('outreach-42')
    await waitFor(() => expect(screen.getByText('Hi Jane, deep-linked draft.')).toBeInTheDocument())
    expect(requestedPath).toContain('/outreach/outreach-42')
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Back to the outreach queue/i })).toBeInTheDocument()
  })

  it('shows a page-level not-found error for a 404', async () => {
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Outreach not found' } }, { status: 404 }),
      ),
    )
    renderDeepLink('missing-id')
    await waitFor(() =>
      expect(screen.getByText('This outreach item could not be found.')).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('on a 409 approve conflict, refetches and displays the true current server state rather than any locally-assumed status', async () => {
    let fetchCount = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        fetchCount += 1
        // First fetch (page load): still PENDING_APPROVAL as far as this
        // tab knows. After the 409, the built-in refetch reveals someone
        // else already rejected it.
        const status = fetchCount === 1 ? 'PENDING_APPROVAL' : 'REJECTED'
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi Jane, ...',
          final_message: null,
          status,
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: fetchCount === 1 ? null : '2026-01-01T00:10:00Z',
          sent_at: null,
        })
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    renderDeepLink()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('This was already decided'))
    // The panel now reflects the refetched truth: REJECTED, not an
    // "approved" state assumed from the click, and the action buttons for
    // an already-decided item are gone.
    await waitFor(() => expect(screen.getByText('Rejected.')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(fetchCount).toBeGreaterThanOrEqual(2)
  })

  it('Generated != Sent, end to end: after a real approve + refetch the page shows "Approved — will be sent shortly." and never "Sent." until a later fetch actually reports SENT', async () => {
    let fetchCount = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        fetchCount += 1
        // Fetch 1: page load, still pending. Fetch 2+ (post-approve
        // invalidation): the record is now genuinely APPROVED server-side
        // — still not SENT, because sending is an independent, later,
        // Kafka-triggered step.
        const status = fetchCount === 1 ? 'PENDING_APPROVAL' : 'APPROVED'
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi Jane, ...',
          final_message: null,
          status,
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: fetchCount === 1 ? null : '2026-01-02T00:00:00Z',
          sent_at: null,
        })
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        // Even the approve endpoint's own response is APPROVED, never SENT.
        HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi Jane, ...',
          final_message: null,
          status: 'APPROVED',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: '2026-01-02T00:00:00Z',
          sent_at: null,
        }),
      ),
    )
    renderDeepLink()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument())
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(screen.getByText('Approved — will be sent shortly.')).toBeInTheDocument(),
    )
    expect(screen.queryByText('Sent.', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Sent', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('Edit then still-actionable: after a real edit + refetch, the page shows the edited message and EDITED status, with Approve/Reject still available', async () => {
    let fetchCount = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        fetchCount += 1
        const edited = fetchCount > 1
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi Jane, original draft.',
          final_message: edited ? 'Hi Jane, edited draft.' : null,
          status: edited ? 'EDITED' : 'PENDING_APPROVAL',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: null,
          sent_at: null,
        })
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () =>
        HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi Jane, original draft.',
          final_message: 'Hi Jane, edited draft.',
          status: 'EDITED',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: null,
          sent_at: null,
        }),
      ),
    )
    renderDeepLink()
    await waitFor(() => expect(screen.getByText('Hi Jane, original draft.')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: /Edit/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save edit' }))

    await waitFor(() => expect(screen.getByText('Hi Jane, edited draft.')).toBeInTheDocument())
    expect(screen.getByText('Edited — still needs your approval or rejection.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })
})
