/**
 * Dashboard-only "needs your review" list. Extracted from `DashboardPage.tsx`
 * in T5 (docs/frontend/frontend-revamp-spec.md), which upgraded it from a
 * single count + link-to-the-queue banner into a real list whose rows deep
 * link to `/outreach/:outreachId` (the ticket's acceptance criterion).
 *
 * Data sources — both already fetched and polled by `DashboardPage` (10s),
 * nothing new is requested here:
 * - `GET /outreach?status=PENDING_APPROVAL` for the drafts themselves.
 * - `GET /applications` for the job each draft belongs to. `OutreachResponse`
 *   carries only `job_id` (no company/title), and `ApplicationResponse`
 *   carries `job_id` alongside `company`/`title`, so the two are joined
 *   client-side on `job_id`. When no application row has surfaced for a
 *   job yet (the Application row is created asynchronously), the row falls
 *   back to the draft's own text rather than inventing a job name.
 *
 * This page reads outreach *data* for a summary display only — it renders no
 * approve/edit/reject control, per docs/frontend/agent-ownership.md. The one
 * action on every row is "go to the review screen".
 *
 * Owner: frontend-opportunities-agent.
 */

import { Link } from 'react-router-dom'
import { ChevronRight, Inbox } from 'lucide-react'
import { Card, ErrorState, Skeleton, StatusBadge } from '../../components'
import { formatDate } from '../../utils/format'
import type { ApplicationResponse, OutreachResponse } from '../../api/types'

/** How many rows are shown inline before deferring to the full queue. */
const MAX_VISIBLE = 4

function truncate(text: string, max = 96): string {
  const collapsed = text.replace(/\s+/g, ' ').trim()
  return collapsed.length > max ? `${collapsed.slice(0, max - 1)}…` : collapsed
}

type NeedsAttentionListProps = {
  items: OutreachResponse[]
  /** `GET /applications` rows keyed by `job_id`, for the job-name join. */
  applicationsByJobId: Map<string, ApplicationResponse>
  isLoading: boolean
  errorMessage?: string
  onRetry: () => void
}

export function NeedsAttentionList({
  items,
  applicationsByJobId,
  isLoading,
  errorMessage,
  onRetry,
}: NeedsAttentionListProps) {
  if (isLoading) {
    return (
      <div role="status" aria-label="Loading outreach awaiting your review" className="mb-8">
        <Skeleton className="h-20 rounded-lg" />
      </div>
    )
  }

  if (errorMessage) {
    // Panel-scoped, per docs/frontend/error-handling.md — a failed secondary
    // query must not take the rest of the Dashboard down with it.
    return (
      <div className="mb-8">
        <ErrorState message={errorMessage} onRetry={onRetry} />
      </div>
    )
  }

  // Nothing waiting is the good, quiet case: the Dashboard says nothing
  // rather than spending a card on an empty inbox, which would compete with
  // the page's one primary action.
  if (items.length === 0) {
    return null
  }

  const visible = items.slice(0, MAX_VISIBLE)
  const overflow = items.length - visible.length

  return (
    <section aria-labelledby="needs-attention-heading" className="mb-8">
      <Card className="border-l-4 border-l-status-attention p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-5 pb-3">
          <div className="flex items-center gap-2">
            <Inbox className="h-4 w-4 text-status-attention" aria-hidden />
            <h2 id="needs-attention-heading" className="text-sm font-medium text-gray-900">
              Needs your review
            </h2>
          </div>
          <p className="text-xs text-gray-500">
            {items.length} outreach draft{items.length === 1 ? '' : 's'} can&apos;t be sent until you approve
            {items.length === 1 ? ' it' : ' them'}.
          </p>
        </div>

        <ul className="border-t border-gray-100">
          {visible.map((item) => {
            const application = applicationsByJobId.get(item.job_id)
            const message = item.final_message ?? item.draft_message
            return (
              <li key={item.id} className="border-b border-gray-100 last:border-b-0">
                <Link
                  to={`/outreach/${item.id}`}
                  className="flex items-center justify-between gap-4 px-6 py-3 hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">
                      {application ? `${application.company} — ${application.title}` : 'Outreach draft'}
                    </p>
                    <p className="truncate text-sm text-gray-600">{truncate(message)}</p>
                    <p className="mt-0.5 text-xs text-gray-500">Drafted {formatDate(item.generated_at)}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <StatusBadge status={item.status} />
                    <ChevronRight className="h-4 w-4 text-gray-400" aria-hidden />
                  </div>
                </Link>
              </li>
            )
          })}
        </ul>

        {overflow > 0 ? (
          <div className="border-t border-gray-100 px-6 py-3">
            <Link to="/outreach" className="text-sm font-medium text-brand hover:underline">
              {overflow} more in the outreach queue
            </Link>
          </div>
        ) : null}
      </Card>
    </section>
  )
}
