/**
 * Route: /outreach/:outreachId — deep link into the same review panel the
 * queue uses, see
 * docs/frontend/routes.md#outreachoutreachid--outreach-review-deep-link.
 * `GET /outreach/{id}` directly, not filtered from the list — this route
 * exists purely so the review UI is addressable on its own (e.g. from a
 * Dashboard "needs attention" link, or a bookmark), not a second
 * implementation of the review UI. `OutreachReviewPanel` is imported
 * unchanged from `OutreachQueuePage`'s implementation.
 *
 * Per docs/frontend/async-workflows.md's polling table, a single outreach
 * item is fetch-once + `refetchOnWindowFocus` (no interval) —
 * `useOutreachItem` already defaults to that, so no `refetchInterval` is
 * passed here.
 *
 * This page has no `applicationId` to pass down to the panel/approve
 * mutation: `OutreachResponse` only carries `job_id`, not an
 * application id, and there is no "get application by job_id" lookup (see
 * docs/frontend/api-mapping.md#outreach-service and
 * `OutreachReviewPanel`'s own doc comment) — that's a documented, optional
 * gap, not a bug; approve still works, just with the less targeted
 * `applications`/`outreach` invalidation instead of the more targeted
 * single-application one.
 *
 * It also passes no `onConflict`: this page's view of the record *is*
 * `useOutreachItem`, which the 409 handling in `api/outreach.ts` already
 * invalidates, so adding one here would only duplicate that refetch. The
 * queue needs the prop because it reads the record from list queries
 * instead.
 *
 * T9 (docs/frontend/frontend-revamp-spec.md) restyle: the "back to queue"
 * link moved above the header where a deep-linked page's escape hatch
 * belongs (it is the only such link on the page — not duplicated at the
 * bottom), the page is constrained to a readable column, and the loading
 * state is a card-shaped skeleton matching the panel it resolves into
 * rather than three loose bars.
 *
 * Owner: frontend-outreach-agent.
 */

import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Card, ErrorState, PageHeader, Skeleton } from '../../components'
import { useOutreachItem } from '../../api/outreach'
import { toApiError } from '../../api/client'
import { OutreachReviewPanel } from './OutreachReviewPanel'

export function OutreachReviewPage() {
  const { outreachId } = useParams<{ outreachId: string }>()
  const query = useOutreachItem(outreachId ?? '')

  const apiError = query.error ? toApiError(query.error) : null

  return (
    <div className="max-w-3xl">
      <Link
        to="/outreach"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        Back to the outreach queue
      </Link>

      <div className="mt-3">
        <PageHeader
          title="Review Outreach"
          description="Approve, edit, or reject this draft. Nothing is sent until you approve it."
        />
      </div>

      {query.isLoading ? (
        <Card className="flex flex-col gap-4" aria-busy="true">
          <div className="flex items-start justify-between gap-4">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-5 w-24" />
          </div>
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-9 w-56" />
        </Card>
      ) : query.isError ? (
        <ErrorState
          message={
            apiError?.status === 404 ? 'This outreach item could not be found.' : (apiError?.message ?? 'Something went wrong.')
          }
          onRetry={() => void query.refetch()}
        />
      ) : query.data ? (
        <OutreachReviewPanel outreach={query.data} />
      ) : null}
    </div>
  )
}
