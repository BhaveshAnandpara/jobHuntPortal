/**
 * Route: /opportunities — see docs/frontend/routes.md#opportunities--opportunities.
 * Owner: frontend-opportunities-agent.
 *
 * Single unfiltered `GET /applications` fetch (5s always-on poll, per
 * docs/frontend/async-workflows.md's table), partitioned client-side into
 * the four status-grouping tabs from
 * docs/frontend/user-flows.md#application-tracker-ux. Two distinct empty
 * states: "no opportunities at all" vs. "no opportunities match this
 * filter" (the second offers an obvious fix — clear the filter — the first
 * doesn't).
 *
 * The job-URL submit form is lightly duplicated here (see
 * routes.md's "possibly duplicated lightly on /opportunities" note) so a
 * user who lands here directly still has the primary action available. On
 * a successful submission from here (or from the Dashboard), the ingested
 * job's company/title/id arrives via router state and is shown as an
 * optimistic "submitted, analyzing…" banner until polling surfaces the real
 * `Application` row for that job.
 */

import { useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Briefcase } from 'lucide-react'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  StatusBadge,
  Table,
  type TableColumn,
} from '../../components'
import { useCurrentUserId } from '../../hooks/identity'
import { useApplications } from '../../api/tracking'
import { pollAlways } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { formatDate, formatScorePercent } from '../../utils/format'
import type { ApplicationResponse } from '../../api/types'
import { JobUrlSubmitForm } from './JobUrlSubmitForm'
import { matchesStatusTab, STATUS_TABS, type StatusTab } from './pipeline'

type SubmittedJobState = {
  submittedJob?: { jobId: string; company?: string | null; title?: string | null }
}

export function OpportunitiesPage() {
  const { userId } = useCurrentUserId()
  const navigate = useNavigate()
  const location = useLocation()
  const [tab, setTab] = useState<StatusTab>('active')

  const applications = useApplications(userId ?? '', undefined, {
    refetchInterval: pollAlways(5000),
  })

  const applicationList = useMemo(() => applications.data ?? [], [applications.data])
  const filtered = useMemo(
    () => applicationList.filter((application) => matchesStatusTab(application.status, tab)),
    [applicationList, tab],
  )

  const submittedJob = (location.state as SubmittedJobState | null)?.submittedJob
  const showSubmittedBanner =
    Boolean(submittedJob) &&
    !applicationList.some((application) => application.job_id === submittedJob?.jobId)

  const columns: TableColumn<ApplicationResponse>[] = [
    {
      key: 'opportunity',
      header: 'Opportunity',
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium text-gray-900">{row.title}</p>
          <p className="truncate text-xs text-gray-500">{row.company}</p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'match_score',
      header: 'Match',
      render: (row) => (row.match_score != null ? formatScorePercent(row.match_score) : '—'),
    },
    {
      key: 'updated_at',
      header: 'Last activity',
      render: (row) => formatDate(row.updated_at),
    },
  ]

  return (
    <div>
      <PageHeader title="Opportunities" description="Every opportunity you're pursuing, in one place." />

      <Card className="mb-6">
        <JobUrlSubmitForm />
      </Card>

      {showSubmittedBanner ? (
        <div className="mb-4 rounded-lg border border-status-progress-bg bg-status-progress-bg px-4 py-3 text-sm text-status-progress">
          {submittedJob?.company ?? 'Your job'}
          {submittedJob?.title ? ` — ${submittedJob.title}` : ''} submitted. Analyzing…
        </div>
      ) : null}

      {applications.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : applications.isError ? (
        <ErrorState message={toApiError(applications.error).message} onRetry={() => applications.refetch()} />
      ) : applicationList.length === 0 ? (
        <EmptyState
          icon={<Briefcase className="h-8 w-8" />}
          title="No opportunities yet"
          description="Paste a job URL above to get started."
        />
      ) : (
        <>
          <div role="tablist" aria-label="Filter by status" className="mb-4 flex gap-1 border-b border-gray-200">
            {STATUS_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'border-brand text-brand'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              title="No opportunities match this filter"
              description="Try a different status tab."
              action={
                <Button type="button" variant="secondary" onClick={() => setTab('all')}>
                  Clear filter
                </Button>
              }
            />
          ) : (
            <Table
              aria-label="Opportunities"
              columns={columns}
              rows={filtered}
              getRowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/opportunities/${row.id}`)}
            />
          )}
        </>
      )}
    </div>
  )
}
