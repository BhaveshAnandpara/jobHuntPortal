/**
 * Component tests for `ContactsPanel` — see
 * docs/frontend/agent-ownership.md's frontend-contacts-agent entry and
 * docs/frontend/user-flows.md#contact-discovery-ux for the contract this
 * verifies against.
 *
 * Owner: frontend-contacts-agent.
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { createTestQueryClient } from '../../api/test-utils'
import type { ContactResponse } from '../../api/types'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setCurrentUserId } from '../../hooks/identity'
import { ContactsPanel, type ContactsPanelProps } from './ContactsPanel'

function renderPanel(props: ContactsPanelProps) {
  const queryClient = createTestQueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <ContactsPanel {...props} />
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

function contactsHandler(contacts: ContactResponse[]) {
  return http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => HttpResponse.json(contacts))
}

function contact(overrides: Partial<ContactResponse> = {}): ContactResponse {
  return {
    id: 'contact-1',
    full_name: 'Jane Doe',
    headline: 'Senior Engineer',
    company: 'Acme Robotics',
    contact_type: 'HIRING_MANAGER',
    profile_url: 'https://example.com/jane',
    relevance_score: 8.5,
    status: 'RANKED',
    ...overrides,
  }
}

beforeEach(() => {
  setCurrentUserId('user-1')
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ContactsPanel — black-box contract', () => {
  it('renders correctly when given only jobId, with no Application/ApplicationStatus prop', async () => {
    server.use(contactsHandler([contact()]))

    // Intentionally only passing the one required prop — this is the
    // contract frontend-opportunities-agent builds against.
    renderPanel({ jobId: 'job-1' })

    expect(screen.getByText('Contacts')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())

    // No company/title supplied -> the search-trigger action must not be
    // offered (it can't supply a valid request body), proving the panel
    // degrades gracefully rather than assuming opportunity-level data.
    expect(screen.queryByRole('button', { name: /search again/i })).not.toBeInTheDocument()
  })
})

describe('ContactsPanel — loading state', () => {
  it('shows a loading skeleton before the contacts query resolves', () => {
    server.use(contactsHandler([contact()]))
    const { container } = renderPanel({ jobId: 'job-1' })

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.queryByText('Jane Doe')).not.toBeInTheDocument()
  })
})

describe('ContactsPanel — ranked contacts', () => {
  it('renders multiple contacts with correct fields, highest relevance_score first', async () => {
    server.use(
      contactsHandler([
        contact({ id: 'c-low', full_name: 'Low Score', relevance_score: 3.0 }),
        contact({ id: 'c-high', full_name: 'High Score', relevance_score: 9.2 }),
        contact({ id: 'c-mid', full_name: 'Mid Score', relevance_score: 6.4 }),
      ]),
    )

    renderPanel({ jobId: 'job-1' })

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3))
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('High Score')
    expect(items[1]).toHaveTextContent('Mid Score')
    expect(items[2]).toHaveTextContent('Low Score')
  })

  it('renders relevance score and status badge', async () => {
    server.use(contactsHandler([contact({ relevance_score: 8.5, status: 'RANKED' })]))
    renderPanel({ jobId: 'job-1' })

    await waitFor(() => expect(screen.getByText('8.5/10')).toBeInTheDocument())
    expect(screen.getByText('Ranked')).toBeInTheDocument()
  })

  it('renders profile_url as a link only when present', async () => {
    server.use(
      contactsHandler([
        contact({ id: 'has-url', full_name: 'Has Url', profile_url: 'https://example.com/has-url' }),
        contact({ id: 'no-url', full_name: 'No Url', profile_url: null }),
      ]),
    )

    renderPanel({ jobId: 'job-1' })
    await waitFor(() => expect(screen.getByText('Has Url')).toBeInTheDocument())

    const links = screen.getAllByRole('link', { name: /view profile/i })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'https://example.com/has-url')
  })
})

describe('ContactsPanel — empty state', () => {
  it('shows EmptyState (not ErrorState) for a valid 200 empty list', async () => {
    server.use(contactsHandler([]))
    renderPanel({ jobId: 'job-1' })

    await waitFor(() =>
      expect(screen.getByText('No relevant contacts found for this company yet')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('ContactsPanel — API error', () => {
  it('shows ErrorState and retries the query on demand', async () => {
    let callCount = 0
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => {
        callCount += 1
        if (callCount === 1) {
          return HttpResponse.json(
            { detail: { code: 'UNKNOWN_ERROR', message: 'Contacts service unavailable.' } },
            { status: 500 },
          )
        }
        return HttpResponse.json([contact()])
      }),
    )

    renderPanel({ jobId: 'job-1' })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Contacts service unavailable.')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('ContactsPanel — manual search trigger', () => {
  it('fires useTriggerContactSearch() with the caller-supplied company/title/location', async () => {
    server.use(contactsHandler([]))
    let receivedBody: Record<string, unknown> | undefined
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/contacts/search`, async ({ request }) => {
        receivedBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ job_id: 'job-1', requested_at: '2026-01-01T00:00:00Z' }, { status: 202 })
      }),
    )

    renderPanel({ jobId: 'job-1', company: 'Acme Robotics', title: 'Senior Backend Engineer', location: 'Remote' })
    await waitFor(() => expect(screen.getByText('No relevant contacts found for this company yet')).toBeInTheDocument())

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /search again/i }))

    await waitFor(() =>
      expect(receivedBody).toEqual({
        user_id: 'user-1',
        company: 'Acme Robotics',
        title: 'Senior Backend Engineer',
        location: 'Remote',
      }),
    )
  })

  it('retries after a failed search trigger', async () => {
    server.use(contactsHandler([]))
    let attempt = 0
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/contacts/search`, () => {
        attempt += 1
        if (attempt === 1) {
          return HttpResponse.json(
            { detail: { code: 'VALIDATION_ERROR', message: 'Search request failed.' } },
            { status: 400 },
          )
        }
        return HttpResponse.json({ job_id: 'job-1', requested_at: '2026-01-01T00:00:00Z' }, { status: 202 })
      }),
    )

    renderPanel({ jobId: 'job-1', company: 'Acme Robotics', title: 'Senior Backend Engineer' })
    await waitFor(() => expect(screen.getByText('No relevant contacts found for this company yet')).toBeInTheDocument())

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /search again/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Search request failed.')

    await user.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(attempt).toBe(2))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('shows a lightweight in-progress indicator and polls briefly after a successful trigger, then stops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let getCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => {
        getCalls += 1
        return HttpResponse.json([])
      }),
      http.post(`${API_BASE_URL}/jobs/:jobId/contacts/search`, () =>
        HttpResponse.json({ job_id: 'job-1', requested_at: '2026-01-01T00:00:00Z' }, { status: 202 }),
      ),
    )

    renderPanel({ jobId: 'job-1', company: 'Acme Robotics', title: 'Senior Backend Engineer', location: 'Remote' })
    await waitFor(() => expect(getCalls).toBeGreaterThanOrEqual(1))

    fireEvent.click(screen.getByRole('button', { name: /search again/i }))
    await waitFor(() => expect(screen.getByText('Searching for contacts…')).toBeInTheDocument())

    const callsAfterTrigger = getCalls
    await vi.advanceTimersByTimeAsync(3_100)
    await waitFor(() => expect(getCalls).toBeGreaterThan(callsAfterTrigger))

    // Past the ~30s polling window, the panel should stop polling and hide
    // the in-progress indicator again.
    await vi.advanceTimersByTimeAsync(30_000)
    await waitFor(() => expect(screen.queryByText('Searching for contacts…')).not.toBeInTheDocument())

    vi.useRealTimers()
  })
})

describe('ContactsPanel — profession independence', () => {
  it('renders a software-engineering-flavored contact list with no special-cased logic', async () => {
    server.use(
      contactsHandler([
        contact({
          id: 'sw-1',
          full_name: 'Alex Kim',
          headline: 'Engineering Manager',
          company: 'Acme Software',
          contact_type: 'TEAM_LEAD',
          relevance_score: 9.1,
        }),
        contact({
          id: 'sw-2',
          full_name: 'Priya Rao',
          headline: 'Senior Software Engineer',
          company: 'Acme Software',
          contact_type: 'PRACTITIONER',
          relevance_score: 8.7,
        }),
        contact({
          id: 'sw-3',
          full_name: 'Sam Lee',
          headline: 'Technical Recruiter',
          company: 'Acme Software',
          contact_type: 'RECRUITER',
          relevance_score: 7.5,
        }),
      ]),
    )

    renderPanel({ jobId: 'job-1' })

    await waitFor(() => expect(screen.getByText('Alex Kim')).toBeInTheDocument())
    expect(screen.getByText('Team Lead')).toBeInTheDocument()
    expect(screen.getByText('Practitioner')).toBeInTheDocument()
    expect(screen.getByText('Recruiter')).toBeInTheDocument()
  })

  it('renders a mechanical-engineering-flavored contact list with no special-cased logic', async () => {
    server.use(
      contactsHandler([
        contact({
          id: 'me-1',
          full_name: 'Carla Diaz',
          headline: 'Mechanical Design Lead',
          company: 'TorqueWorks',
          contact_type: 'TEAM_LEAD',
          relevance_score: 8.9,
        }),
        contact({
          id: 'me-2',
          full_name: 'Ben Ortiz',
          headline: 'Manufacturing Engineer',
          company: 'TorqueWorks',
          contact_type: 'PRACTITIONER',
          relevance_score: 7.2,
        }),
        contact({
          id: 'me-3',
          full_name: 'Dana White',
          headline: 'VP of Engineering',
          company: 'TorqueWorks',
          contact_type: 'EXECUTIVE',
          relevance_score: 6.8,
        }),
      ]),
    )

    renderPanel({ jobId: 'job-1' })

    await waitFor(() => expect(screen.getByText('Carla Diaz')).toBeInTheDocument())
    expect(screen.getByText('Team Lead')).toBeInTheDocument()
    expect(screen.getByText('Practitioner')).toBeInTheDocument()
    expect(screen.getByText('Executive')).toBeInTheDocument()
  })

  it('renders an HR-flavored contact list with no special-cased logic', async () => {
    server.use(
      contactsHandler([
        contact({
          id: 'hr-1',
          full_name: 'Morgan Blake',
          headline: 'HR Business Partner',
          company: 'PeopleFirst Inc',
          contact_type: 'DEPARTMENT_LEADER',
          relevance_score: 8.2,
        }),
        contact({
          id: 'hr-2',
          full_name: 'Jordan Ellis',
          headline: 'Talent Acquisition Lead',
          company: 'PeopleFirst Inc',
          contact_type: 'HIRING_MANAGER',
          relevance_score: 7.9,
        }),
        contact({
          id: 'hr-3',
          full_name: 'Riley Chen',
          headline: 'Junior Recruiter',
          company: 'PeopleFirst Inc',
          contact_type: 'RECRUITER',
          relevance_score: 6.1,
        }),
      ]),
    )

    renderPanel({ jobId: 'job-1' })

    await waitFor(() => expect(screen.getByText('Morgan Blake')).toBeInTheDocument())
    expect(screen.getByText('Department Leader')).toBeInTheDocument()
    expect(screen.getByText('Hiring Manager')).toBeInTheDocument()
    expect(screen.getByText('Recruiter')).toBeInTheDocument()
  })
})
