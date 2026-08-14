/**
 * See docs/frontend/agent-ownership.md's frontend-contacts-agent entry and
 * docs/frontend/user-flows.md#contact-discovery-ux.
 *
 * Owner: frontend-contacts-agent.
 * Input: `jobId` — the only required prop. This component must stay usable
 *        with only a job id, nothing else (no `Application`/`ApplicationStatus`
 *        knowledge), so it stays a true black box for the page that embeds
 *        it — see docs/frontend/agent-ownership.md's MUST NOT clause.
 *        `company`/`title`/`location` are optional and, when supplied by the
 *        parent (which already has the job's data), enable the manual
 *        "Search again" action — Contact Discovery Service cannot look these
 *        up itself, see api-mapping.md#contact-discovery-service.
 * Output: self-contained rendering of GET /jobs/{job_id}/contacts (loading /
 *         search-in-progress / empty / populated / error), plus the manual
 *         re-trigger action (POST /jobs/{job_id}/contacts/search).
 * Consumers: frontend-opportunities-agent's OpportunityDetailPage
 *            (imported, never modified by that agent).
 */

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Search } from 'lucide-react'
import { Button, Card, EmptyState, ErrorState, Skeleton, Spinner, StatusBadge } from '../../components'
import { useCurrentUserId } from '../../hooks/identity'
import { pollAlways } from '../../hooks/usePolling'
import { listContacts, useContacts, useTriggerContactSearch } from '../../api/contacts'
import { queryKeys } from '../../api/queryKeys'
import type { ContactResponse, ContactType } from '../../api/types'

// How long, and how often, this panel polls after a manual "Search again"
// trigger before giving up and going quiet again — see this file's
// in-component polling block below for why this isn't a hand-rolled
// setInterval.
const SEARCH_POLL_WINDOW_MS = 30_000
const SEARCH_POLL_INTERVAL_MS = 3_000

/**
 * `ContactType` -> human-readable label. Deliberately local to this feature
 * folder (not src/utils/status.ts, which is frontend-design-agent's file
 * scoped to lifecycle enums only) — `ContactType` is a functional-tier
 * category ("why might this person be useful to contact"), not a lifecycle
 * status, so it doesn't belong in that shared mapping. See
 * docs/frontend/agent-ownership.md's frontend-contacts-agent entry.
 */
const CONTACT_TYPE_LABELS: Record<ContactType, string> = {
  PRACTITIONER: 'Practitioner',
  TEAM_LEAD: 'Team Lead',
  HIRING_MANAGER: 'Hiring Manager',
  RECRUITER: 'Recruiter',
  EXECUTIVE: 'Executive',
  DEPARTMENT_LEADER: 'Department Leader',
  OTHER: 'Other',
}

export type ContactsPanelProps = {
  jobId: string
  company?: string
  title?: string
  location?: string
}

export function ContactsPanel({ jobId, company, title, location }: ContactsPanelProps) {
  const { userId } = useCurrentUserId()
  const contactsQuery = useContacts(jobId)
  const triggerSearch = useTriggerContactSearch()
  const [isSearching, setIsSearching] = useState(false)

  // Short-lived polling window scoped to this component only, per
  // hooks/usePolling.ts's convention (a `useQuery`'s `refetchInterval` is
  // driven by `pollAlways`/`pollUntil`; nothing hand-rolls its own
  // `setInterval` for scheduling a refetch). This second `useQuery`
  // subscribes to the *same* query key `useContacts` already reads — it
  // exists purely to drive background refetches into that shared cache
  // entry while `isSearching` is true, not to introduce a second data
  // source; the panel always renders from `contactsQuery` above.
  useQuery({
    queryKey: queryKeys.contacts(jobId),
    queryFn: () => listContacts(jobId),
    enabled: Boolean(jobId) && isSearching,
    refetchInterval: isSearching ? pollAlways(SEARCH_POLL_INTERVAL_MS) : false,
  })

  // Turns the polling window off again after ~30s. This only toggles *when*
  // the panel stops asking for background refetches — the refetching itself
  // is still entirely `refetchInterval`-driven above, not this timer.
  useEffect(() => {
    if (!isSearching) return
    const timeout = setTimeout(() => setIsSearching(false), SEARCH_POLL_WINDOW_MS)
    return () => clearTimeout(timeout)
  }, [isSearching])

  const canSearch = Boolean(userId && company && title)

  function handleSearchAgain() {
    if (!userId || !company || !title) return
    triggerSearch.mutate(
      { jobId, body: { user_id: userId, company, title, location: location ?? null } },
      { onSuccess: () => setIsSearching(true) },
    )
  }

  const sortedContacts = useMemo<ContactResponse[]>(() => {
    // ContactRankingResult.contacts is documented as already ordered by the
    // backend; sorting again here is presentation-only defensive display
    // logic, not recomputing anything the backend didn't already decide —
    // see docs/frontend/user-flows.md#contact-discovery-ux.
    const data = contactsQuery.data ?? []
    return [...data].sort((a, b) => b.relevance_score - a.relevance_score)
  }, [contactsQuery.data])

  const searchAgainButton = canSearch ? (
    <Button
      variant="secondary"
      onClick={handleSearchAgain}
      isLoading={triggerSearch.isPending}
      disabled={isSearching}
    >
      <Search className="h-4 w-4" aria-hidden />
      Search again
    </Button>
  ) : null

  if (contactsQuery.isPending) {
    return (
      <Card>
        <p className="text-sm font-semibold text-gray-900">Contacts</p>
        <div className="mt-4 space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      </Card>
    )
  }

  if (contactsQuery.isError) {
    return (
      <Card>
        <p className="text-sm font-semibold text-gray-900">Contacts</p>
        <div className="mt-4">
          <ErrorState message={contactsQuery.error.message} onRetry={() => void contactsQuery.refetch()} />
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-gray-900">Contacts</p>
        {sortedContacts.length > 0 ? searchAgainButton : null}
      </div>

      {isSearching ? (
        <div className="mt-3 flex items-center gap-2 text-sm text-gray-500">
          <Spinner className="h-4 w-4" label="Searching for contacts" />
          Searching for contacts…
        </div>
      ) : null}

      {triggerSearch.isError ? (
        <div className="mt-3">
          <ErrorState message={triggerSearch.error.message} onRetry={handleSearchAgain} />
        </div>
      ) : null}

      {sortedContacts.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            title="No relevant contacts found for this company yet"
            action={searchAgainButton ?? undefined}
          />
        </div>
      ) : (
        <ul className="mt-4 space-y-3">
          {sortedContacts.map((contact) => (
            <li key={contact.id}>
              <ContactRow contact={contact} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ContactRow({ contact }: { contact: ContactResponse }) {
  const scorePercent = Math.max(0, Math.min(100, (contact.relevance_score / 10) * 100))

  return (
    <div className="rounded-md border border-gray-200 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-gray-900">{contact.full_name}</p>
          {contact.headline ? <p className="text-sm text-gray-500">{contact.headline}</p> : null}
          <p className="text-sm text-gray-500">{contact.company}</p>
        </div>
        <StatusBadge status={contact.status} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700">
          {CONTACT_TYPE_LABELS[contact.contact_type]}
        </span>

        <div
          className="flex items-center gap-2"
          aria-label={`Relevance score ${contact.relevance_score.toFixed(1)} out of 10`}
        >
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-200">
            <div className="h-full rounded-full bg-brand" style={{ width: `${scorePercent}%` }} />
          </div>
          <span className="text-xs text-gray-500">{contact.relevance_score.toFixed(1)}/10</span>
        </div>

        {contact.profile_url ? (
          <a
            href={contact.profile_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
          >
            View profile
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        ) : null}
      </div>
    </div>
  )
}
