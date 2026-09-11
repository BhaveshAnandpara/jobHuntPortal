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
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. The important part is
 * the Section 2.6 invariant: **Approved and Sent are different states and
 * must never read as the same one.** Rather than word that again here (and
 * risk drifting from the `/outreach` surface that says it first), this panel
 * renders T9's shared `outreachCopy.ts` helpers — `getStatusHelperCopy` for
 * the full sentence and `getDeliveryNote` for the short delivery line. An
 * `APPROVED` record therefore reads "Awaiting send · Approved — will be sent
 * shortly" here exactly as it does in the outreach queue and review panel,
 * and a `SENT` one reads "Delivered <time> · Sent." There is no wording in
 * this file that the outreach surface doesn't already own.
 *
 * The other T8 change is the progressive gate: before outreach generation has
 * run, this says so instead of showing the same "hasn't been generated yet"
 * line it would show for a job whose generation ran and produced nothing —
 * see lifecycleStage.ts.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Link } from 'react-router-dom'
import { MailCheck } from 'lucide-react'
import { ErrorState, Skeleton, StatusBadge } from '../../components'
import { DetailPanel, StageNotReached } from './DetailPanel'
import { getDeliveryNote, getStatusHelperCopy } from '../outreach/outreachCopy'
import type { OutreachResponse, OutreachStatus } from '../../api/types'
import { formatDate } from '../../utils/format'

type OutreachSummaryPanelProps = {
  outreach: OutreachResponse | undefined
  /** False only when outreach generation provably hasn't run yet — see lifecycleStage.ts. */
  isAvailable: boolean
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

/** The two statuses that still need a human decision — the only ones worth linking into. */
const PENDING_STATUSES: OutreachStatus[] = ['PENDING_APPROVAL', 'EDITED']

export function OutreachSummaryPanel({
  outreach,
  isAvailable,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: OutreachSummaryPanelProps) {
  // An existing record always wins over the stage gate: if the outreach list
  // came back with a draft for this job, it exists, whatever the application
  // status currently reads.
  if (!outreach && !isAvailable && !isLoading && !isError) {
    return (
      <DetailPanel title="Outreach">
        <StageNotReached
          icon={<MailCheck className="h-5 w-5" aria-hidden />}
          title="No draft yet"
          description="Once a contact is found, a draft message is generated for your review. Nothing is ever sent without your approval."
        />
      </DetailPanel>
    )
  }

  const deliveryNote = outreach ? getDeliveryNote(outreach) : null
  const helperCopy = outreach ? getStatusHelperCopy(outreach.status) : null

  return (
    <DetailPanel title="Outreach" description="Nothing is sent without your approval.">
      {isLoading ? (
        <span role="status" aria-label="Loading outreach">
          <Skeleton className="h-8 w-full rounded-md" />
        </span>
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load outreach.'} onRetry={onRetry} />
      ) : !outreach ? (
        // Generation has run for this opportunity but no record came back —
        // an empty result, not a "hasn't started" one.
        <p className="text-sm text-gray-400">No outreach draft exists for this opportunity.</p>
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={outreach.status} />
              {deliveryNote ? (
                <span className="text-xs font-medium text-gray-600">{deliveryNote}</span>
              ) : null}
            </div>
            {helperCopy ? <p className="mt-1.5 text-sm text-gray-700">{helperCopy}</p> : null}
            <p className="mt-1 text-xs text-gray-500">Generated {formatDate(outreach.generated_at)}</p>
          </div>
          {PENDING_STATUSES.includes(outreach.status) ? (
            <Link
              to={`/outreach/${outreach.id}`}
              className="shrink-0 text-sm font-medium text-brand hover:underline"
            >
              Review
            </Link>
          ) : null}
        </div>
      )}
    </DetailPanel>
  )
}
