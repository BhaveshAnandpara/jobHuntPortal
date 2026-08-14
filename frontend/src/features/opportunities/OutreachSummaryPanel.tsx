/**
 * Outreach context panel — Opportunity Detail. READ-ONLY summary (status
 * badge + generated date) of any outreach linked to this job, with a
 * "Review" link into `/outreach/:outreachId` when one exists and is
 * pending. No approve/reject/edit UI here — that belongs entirely to
 * frontend-outreach-agent's `/outreach` surface, per this agent's explicit
 * "Must Not" boundary.
 *
 * `GET /outreach` has no `job_id` filter (a documented, non-blocking gap —
 * see docs/frontend/api-mapping.md#outreach-service), so the caller fetches
 * the user's full outreach list and filters/sorts to this job client-side;
 * this component only renders the single most-relevant result.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Link } from 'react-router-dom'
import { Card, ErrorState, Skeleton, StatusBadge } from '../../components'
import type { OutreachResponse, OutreachStatus } from '../../api/types'
import { formatDate } from '../../utils/format'

type OutreachSummaryPanelProps = {
  outreach: OutreachResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

const PENDING_STATUSES: OutreachStatus[] = ['PENDING_APPROVAL', 'EDITED']

export function OutreachSummaryPanel({
  outreach,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: OutreachSummaryPanelProps) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Outreach</h2>
      {isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load outreach.'} onRetry={onRetry} />
      ) : !outreach ? (
        <p className="text-sm text-gray-500">Outreach hasn&apos;t been generated yet.</p>
      ) : (
        <div className="flex items-center justify-between gap-4">
          <div>
            <StatusBadge status={outreach.status} />
            <p className="mt-1 text-xs text-gray-500">Generated {formatDate(outreach.generated_at)}</p>
          </div>
          {PENDING_STATUSES.includes(outreach.status) ? (
            <Link to={`/outreach/${outreach.id}`} className="text-sm font-medium text-brand hover:underline">
              Review
            </Link>
          ) : null}
        </div>
      )}
    </Card>
  )
}
