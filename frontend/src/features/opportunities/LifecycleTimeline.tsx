/**
 * Lifecycle Timeline — Opportunity Detail. Renders `useApplicationHistory`
 * in chronological order; every entry is a real `ApplicationHistoryResponse`
 * row already produced by Tracking Service, no frontend-invented states —
 * see docs/frontend/user-flows.md#full-opportunity-lifecycle-what-the-user-sees-at-each-stage.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Card, ErrorState, Skeleton, StatusBadge } from '../../components'
import type { ApplicationHistoryResponse } from '../../api/types'
import { formatDateTime } from '../../utils/format'

type LifecycleTimelineProps = {
  history: ApplicationHistoryResponse[] | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

export function LifecycleTimeline({ history, isLoading, isError, errorMessage, onRetry }: LifecycleTimelineProps) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">History</h2>
      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load history.'} onRetry={onRetry} />
      ) : !history || history.length === 0 ? (
        <p className="text-sm text-gray-500">No history yet.</p>
      ) : (
        <ol className="flex flex-col gap-3 border-l border-gray-200 pl-4">
          {[...history]
            .sort((a, b) => new Date(a.changed_at).getTime() - new Date(b.changed_at).getTime())
            .map((entry) => (
              <li key={entry.id} className="relative">
                <span className="absolute -left-[1.32rem] top-1 h-2 w-2 rounded-full bg-brand" aria-hidden />
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={entry.to_status} />
                  <span className="text-xs text-gray-500">{formatDateTime(entry.changed_at)}</span>
                </div>
                <p className="mt-0.5 text-xs text-gray-500">
                  {entry.from_status ? `From ${entry.from_status} · ` : ''}
                  Triggered by {entry.triggered_by}
                </p>
              </li>
            ))}
        </ol>
      )}
    </Card>
  )
}
