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
 * Owner: frontend-outreach-agent.
 */

import { Link, useParams } from 'react-router-dom'
import { ErrorState, PageHeader, Skeleton } from '../../components'
import { useOutreachItem } from '../../api/outreach'
import { useCurrentUserId } from '../../hooks/identity'
import { toApiError } from '../../api/client'
import { OutreachReviewPanel } from './OutreachReviewPanel'

export function OutreachReviewPage() {
  const { outreachId } = useParams<{ outreachId: string }>()
  const { userId } = useCurrentUserId()
  const query = useOutreachItem(outreachId ?? '')

  const apiError = query.error ? toApiError(query.error) : null

  return (
    <div>
      <PageHeader
        title="Review Outreach"
        description="Approve, edit, or reject this draft before anything is sent."
      />

      {query.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-10 w-1/2" />
        </div>
      ) : query.isError ? (
        <ErrorState
          message={
            apiError?.status === 404 ? 'This outreach item could not be found.' : (apiError?.message ?? 'Something went wrong.')
          }
          onRetry={() => void query.refetch()}
        />
      ) : query.data && userId ? (
        <OutreachReviewPanel outreach={query.data} userId={userId} />
      ) : null}

      <p className="mt-4 text-xs text-gray-500">
        <Link to="/outreach" className="underline hover:text-gray-700">
          Back to the outreach queue
        </Link>
      </p>
    </div>
  )
}
