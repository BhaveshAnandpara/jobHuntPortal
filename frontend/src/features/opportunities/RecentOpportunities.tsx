/**
 * Dashboard-only "recent opportunities" list — the five most recently updated
 * rows from the same `GET /applications` response the pipeline summary counts
 * (no second request, no `?status` filter). Extracted from `DashboardPage.tsx`
 * in T5 (docs/frontend/frontend-revamp-spec.md).
 *
 * Ordering is `updated_at` descending, which is the backend's own
 * last-activity timestamp — not a client-side notion of "interesting". Each
 * row shows only fields the API actually returns (title, company,
 * `updated_at`, `status` via the shared `StatusBadge`); the match score is
 * left to the Opportunities list, where there's room for it next to the other
 * columns.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { StatusBadge } from '../../components'
import { formatDate } from '../../utils/format'
import type { ApplicationResponse } from '../../api/types'

export function RecentOpportunities({ applications }: { applications: ApplicationResponse[] }) {
  return (
    <section aria-labelledby="recent-opportunities-heading">
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2
          id="recent-opportunities-heading"
          className="text-xs font-medium tracking-wide text-gray-500 uppercase"
        >
          Recent opportunities
        </h2>
        <Link to="/opportunities" className="text-sm font-medium text-brand hover:underline">
          View all
        </Link>
      </div>

      <ul className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        {applications.map((application) => (
          <li key={application.id} className="border-b border-gray-100 last:border-b-0">
            <Link
              to={`/opportunities/${application.id}`}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900">{application.title}</p>
                <p className="truncate text-xs text-gray-500">
                  {application.company} · Updated {formatDate(application.updated_at)}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <StatusBadge status={application.status} />
                <ChevronRight className="h-4 w-4 text-gray-400" aria-hidden />
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
