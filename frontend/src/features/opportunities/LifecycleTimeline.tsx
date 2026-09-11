/**
 * Lifecycle Timeline — Opportunity Detail. Renders `useApplicationHistory`
 * in chronological order; every entry is a real `ApplicationHistoryResponse`
 * row already produced by Tracking Service, no frontend-invented states —
 * see docs/frontend/user-flows.md#full-opportunity-lifecycle-what-the-user-sees-at-each-stage.
 *
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. Same one query, same
 * rows, newest last:
 * - the rail is drawn per entry rather than as one border on the list, so the
 *   last entry's line stops at the last dot instead of running past it;
 * - each row says who caused it in plain words ("Automatically" for
 *   `triggered_by: "system"`, "By you" otherwise) — `triggered_by` is a real
 *   field, this only renames its two real values for display;
 * - `from_status` renders through the shared status labels rather than
 *   printing the raw enum (`OUTREACH_GENERATED`) at the user.
 *
 * Owner: frontend-opportunities-agent.
 */

import { ErrorState, StatusBadge } from '../../components'
import { DetailPanel, PanelLoading } from './DetailPanel'
import type { ApplicationHistoryResponse } from '../../api/types'
import { formatDateTime } from '../../utils/format'
import { getStatusPresentation } from '../../utils/status'

type LifecycleTimelineProps = {
  history: ApplicationHistoryResponse[] | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

/**
 * `triggered_by` is a free-form string on the wire, and Tracking Service
 * writes exactly two kinds of value (confirmed in src/tracking/api/routes.py
 * and src/tracking/consumers.py): the literal `"user"` for a manual `PATCH
 * /applications/{id}/status`, or the producing component's name for every
 * event-driven transition. Both are rendered as what they *mean* — the
 * component name is an internal identifier, not information the user can act
 * on, so it collapses to "automatically" rather than being printed raw.
 */
function triggeredByLabel(triggeredBy: string): string {
  return triggeredBy === 'user' ? 'Changed by you' : 'Changed automatically'
}

export function LifecycleTimeline({
  history,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: LifecycleTimelineProps) {
  const ordered = [...(history ?? [])].sort(
    (a, b) => new Date(a.changed_at).getTime() - new Date(b.changed_at).getTime(),
  )

  return (
    <DetailPanel title="History" description="Every status change on this opportunity, oldest first.">
      {isLoading ? (
        <PanelLoading label="Loading history" lines={3} />
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load history.'} onRetry={onRetry} />
      ) : ordered.length === 0 ? (
        <p className="text-sm text-gray-400">No status changes have been recorded yet.</p>
      ) : (
        <ol className="flex flex-col">
          {ordered.map((entry, index) => (
            <li key={entry.id} className="flex gap-3">
              <div className="flex w-2 shrink-0 flex-col items-center pt-1.5">
                <span className="h-2 w-2 shrink-0 rounded-full bg-brand" aria-hidden />
                {index < ordered.length - 1 ? (
                  <span className="w-px grow bg-gray-200" aria-hidden />
                ) : null}
              </div>
              <div className={index < ordered.length - 1 ? 'min-w-0 pb-4' : 'min-w-0'}>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={entry.to_status} />
                  <span className="text-xs text-gray-500">{formatDateTime(entry.changed_at)}</span>
                </div>
                <p className="mt-1 text-xs text-gray-500">
                  {triggeredByLabel(entry.triggered_by)}
                  {entry.from_status
                    ? ` · from ${getStatusPresentation(entry.from_status).label}`
                    : ''}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </DetailPanel>
  )
}
