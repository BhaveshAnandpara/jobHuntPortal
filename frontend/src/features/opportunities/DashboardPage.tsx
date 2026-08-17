/**
 * Route: / — see docs/frontend/routes.md#--dashboard.
 * Owner: frontend-opportunities-agent (primary-action + pipeline-summary
 * sections). The "needs attention" list reading both applications and
 * outreach is cross-feature glue owned by frontend-integration-ui-agent —
 * see docs/frontend/agent-ownership.md's integration-ui-agent entry; this
 * page reads `useOutreachList('PENDING_APPROVAL')` directly per its
 * own agent-ownership.md entry ("reading outreach data for a summary
 * display is fine; you're only forbidden from implementing outreach
 * *actions*"), which is enough for a simple attention count/link without
 * needing the richer cross-feature component integration-ui-agent may add
 * later.
 *
 * Polling: `GET /applications` and `GET /outreach?status=PENDING_APPROVAL`
 * both poll at 10s, always-on — the slowest interval of any page, per
 * docs/frontend/async-workflows.md's table (Dashboard is a summary view,
 * not the primary place a user watches active progress).
 */

import { Link } from 'react-router-dom'
import { Briefcase, Send } from 'lucide-react'
import { Card, EmptyState, ErrorState, PageHeader, Skeleton, StatusBadge } from '../../components'
import { useApplications } from '../../api/tracking'
import { useOutreachList } from '../../api/outreach'
import { pollAlways } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { formatDate } from '../../utils/format'
import { JobUrlSubmitForm } from './JobUrlSubmitForm'
import { matchesStatusTab } from './pipeline'

const RECENT_COUNT = 5

export function DashboardPage() {
  const applications = useApplications(undefined, {
    refetchInterval: pollAlways(10000),
  })
  const pendingOutreach = useOutreachList('PENDING_APPROVAL', {
    refetchInterval: pollAlways(10000),
  })

  const applicationList = applications.data ?? []
  const hasAnyOpportunities = applicationList.length > 0

  const recent = [...applicationList]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, RECENT_COUNT)

  console.log(JSON.parse(JSON.stringify(recent)))

  const activeCount = applicationList.filter((a) => matchesStatusTab(a.status, 'active')).length
  const appliedCount = applicationList.filter((a) => matchesStatusTab(a.status, 'applied')).length
  const closedCount = applicationList.filter((a) => matchesStatusTab(a.status, 'closed')).length

  const pendingOutreachCount = pendingOutreach.data?.length ?? 0

  return (
    <div>
      <PageHeader title="Dashboard" description="Paste a job URL to get started." />

      <Card className="mb-6">
        <JobUrlSubmitForm />
      </Card>

      {pendingOutreachCount > 0 ? (
        <Card className="mb-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-gray-900">Needs your attention</p>
              <p className="mt-1 text-sm text-gray-500">
                {pendingOutreachCount} outreach draft{pendingOutreachCount === 1 ? '' : 's'} waiting for your review.
              </p>
            </div>
            <Link
              to="/outreach"
              className="inline-flex shrink-0 items-center gap-1.5 text-sm font-medium text-brand hover:underline"
            >
              <Send className="h-4 w-4" aria-hidden />
              Review outreach
            </Link>
          </div>
        </Card>
      ) : null}

      <section aria-labelledby="pipeline-summary-heading" className="mb-6">
        <h2 id="pipeline-summary-heading" className="mb-3 text-sm font-semibold text-gray-900">
          Pipeline summary
        </h2>
        {applications.isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : applications.isError ? (
          <ErrorState message={toApiError(applications.error).message} onRetry={() => applications.refetch()} />
        ) : !hasAnyOpportunities ? (
          <EmptyState
            icon={<Briefcase className="h-8 w-8" />}
            title="No opportunities yet"
            description="Paste a job URL above to get started."
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <p className="text-2xl font-semibold text-gray-900">{activeCount}</p>
              <p className="text-sm text-gray-500">Active</p>
            </Card>
            <Card>
              <p className="text-2xl font-semibold text-gray-900">{appliedCount}</p>
              <p className="text-sm text-gray-500">Applied</p>
            </Card>
            <Card>
              <p className="text-2xl font-semibold text-gray-900">{closedCount}</p>
              <p className="text-sm text-gray-500">Closed</p>
            </Card>
          </div>
        )}
      </section>

      {hasAnyOpportunities ? (
        <section aria-labelledby="recent-opportunities-heading">
          <div className="mb-3 flex items-center justify-between">
            <h2 id="recent-opportunities-heading" className="text-sm font-semibold text-gray-900">
              Recent opportunities
            </h2>
            <Link to="/opportunities" className="text-sm font-medium text-brand hover:underline">
              View all
            </Link>
          </div>
          <ul className="flex flex-col gap-2">
            {recent.map((application) => (
              <li key={application.id}>
                <Link
                  to={`/opportunities/${application.id}`}
                  className="flex items-center justify-between gap-4 rounded-lg border border-gray-200 bg-white px-4 py-3 hover:bg-gray-50"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{application.title}</p>
                    <p className="truncate text-sm text-gray-500">
                      {application.company} · {formatDate(application.updated_at)}
                    </p>
                  </div>
                  <StatusBadge status={application.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
