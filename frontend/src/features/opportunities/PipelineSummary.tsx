/**
 * Dashboard-only pipeline summary — the three counts derived from the single
 * `GET /applications` response the page already polls (10s, see
 * `DashboardPage.tsx`). Extracted from `DashboardPage.tsx` in T5
 * (docs/frontend/frontend-revamp-spec.md) so the page file keeps only the
 * queries and this file owns the presentation.
 *
 * Every number here is a `filter(...).length` over real rows using
 * `pipeline.ts`'s `matchesStatusTab` — the same grouping the Opportunities
 * list's tabs use, so a count on this page and a tab on that one can never
 * disagree. There is deliberately **no** trend arrow, sparkline, percentage
 * or week-over-week delta: the API returns a flat list of current rows and
 * nothing else, so any of those would be invented (spec Section 3, item 1).
 *
 * The one-line caption under each count is a plain restatement of that
 * group's real status set from `pipeline.ts` — it exists so "Closed" isn't
 * read as "rejected", not as decoration.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Briefcase } from 'lucide-react'
import { Card, EmptyState, ErrorState, Skeleton } from '../../components'

export type PipelineCounts = {
  active: number
  applied: number
  closed: number
}

type PipelineSummaryProps = {
  counts: PipelineCounts
  isLoading: boolean
  errorMessage?: string
  onRetry: () => void
  /** False when `GET /applications` came back with zero rows. */
  hasAnyOpportunities: boolean
}

const CARDS: { key: keyof PipelineCounts; label: string; caption: string }[] = [
  {
    key: 'active',
    label: 'Active',
    caption: 'Being analyzed, matched, or worked through outreach',
  },
  { key: 'applied', label: 'Applied', caption: 'Applied or interviewing' },
  { key: 'closed', label: 'Closed', caption: 'Offer, rejected, ignored, or withdrawn' },
]

export function PipelineSummary({
  counts,
  isLoading,
  errorMessage,
  onRetry,
  hasAnyOpportunities,
}: PipelineSummaryProps) {
  return (
    <section aria-labelledby="pipeline-summary-heading" className="mb-8">
      <h2
        id="pipeline-summary-heading"
        className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase"
      >
        Pipeline summary
      </h2>

      {isLoading ? (
        // Skeletons are `aria-hidden` by design (see components/Skeleton.tsx),
        // so the wrapper carries the announcement instead of leaving screen
        // readers with three silent boxes.
        <div
          role="status"
          aria-label="Loading pipeline summary"
          className="grid grid-cols-1 gap-4 sm:grid-cols-3"
        >
          <Skeleton className="h-24 rounded-lg" />
          <Skeleton className="h-24 rounded-lg" />
          <Skeleton className="h-24 rounded-lg" />
        </div>
      ) : errorMessage ? (
        <ErrorState message={errorMessage} onRetry={onRetry} />
      ) : !hasAnyOpportunities ? (
        <EmptyState
          icon={<Briefcase className="h-8 w-8" aria-hidden />}
          title="No opportunities yet"
          description="Paste a job URL above to get started. Each posting is fetched, matched against your resume, and tracked here automatically."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {CARDS.map(({ key, label, caption }) => (
            <Card key={key} className="transition-shadow hover:shadow-md">
              <p className="text-2xl font-semibold tabular-nums text-gray-900">{counts[key]}</p>
              <p className="mt-0.5 text-sm font-medium text-gray-900">{label}</p>
              <p className="mt-1 text-xs text-gray-500">{caption}</p>
            </Card>
          ))}
        </div>
      )}
    </section>
  )
}
