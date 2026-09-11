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
 *
 * T7 (docs/frontend/frontend-revamp-spec.md) restyle. No request or response
 * handling changed — same one query, same 5s interval, same client-side
 * partition; only presentation and which states are reachable:
 * - The filter control moved to `StatusFilterTabs.tsx` (a segmented control
 *   with a real per-tab row count and full arrow-key support) and the row
 *   definition to `OpportunityTable.tsx`, leaving this file the query, the
 *   state machine between its states, and nothing else.
 * - **Switching tabs never re-fetches.** Filtering is a `filter(...)` over
 *   the rows already in memory under one unchanging query key, so
 *   `isLoading` — which is only ever true for the very first fetch — cannot
 *   flip back to true on a tab click. That is what makes T7's "no full-page
 *   loading flash" criterion hold structurally rather than by luck.
 * - The two empty states now read as different situations, not two spellings
 *   of "nothing here": the no-rows-at-all state points at this page's own
 *   copy of the primary action (or at `/resumes` when there's no analyzed
 *   resume for a submitted job to be matched against, which is the real
 *   blocker in that case — the same `useJobSubmissionReadiness` predicate
 *   the submit form itself uses, so no second API call), while the
 *   no-rows-in-this-tab state names the tab, says how many rows exist
 *   elsewhere, and offers the one-click fix.
 * - Rows are ordered by `updated_at` descending — the backend's own
 *   last-activity timestamp — so the list has a defined order instead of
 *   whatever order the API happened to return.
 */

import { useMemo, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Briefcase, FileText, Loader2, SlidersHorizontal } from 'lucide-react'
import { Button, Card, EmptyState, ErrorState, PageHeader, Skeleton } from '../../components'
import { useApplications } from '../../api/tracking'
import { pollAlways } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { JobUrlSubmitForm } from './JobUrlSubmitForm'
import { OpportunityTable } from './OpportunityTable'
import { StatusFilterTabs, tabId, type StatusTabCounts } from './StatusFilterTabs'
import { useJobSubmissionReadiness } from './jobSubmissionReadiness'
import { STATUS_TABS, matchesStatusTab, type StatusTab } from './pipeline'

type SubmittedJobState = {
  submittedJob?: { jobId: string; company?: string | null; title?: string | null }
}

/** The tabs control this region; it is what `aria-controls` points at. */
const LIST_REGION_ID = 'opportunities-list'

/**
 * Mirrors the shared `Button`'s primary treatment for a link that has to be a
 * real `<a>` (it navigates). `Button` always renders a `<button>`, and adding
 * an `asChild`/`href` escape hatch to it would be a change to
 * `src/components`, outside this ticket's file scope — so the classes come
 * from the same `@theme` tokens `Button` uses, never a one-off color. Same
 * approach, and same constant, as `DashboardPage.tsx`.
 */
const PRIMARY_LINK_CLASSES =
  'inline-flex items-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-hover focus-visible:ring-2 focus-visible:ring-ring'

export function OpportunitiesPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [tab, setTab] = useState<StatusTab>('active')

  const applications = useApplications(undefined, {
    refetchInterval: pollAlways(5000),
  })
  const { isBlockedOnMissingResume } = useJobSubmissionReadiness()

  const applicationList = useMemo(() => applications.data ?? [], [applications.data])

  // Most recently active first — `updated_at` is the backend's own
  // last-activity timestamp, not a client-side notion of "interesting".
  const ordered = useMemo(
    () =>
      [...applicationList].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      ),
    [applicationList],
  )

  const filtered = useMemo(
    () => ordered.filter((application) => matchesStatusTab(application.status, tab)),
    [ordered, tab],
  )

  const counts = useMemo(
    () =>
      STATUS_TABS.reduce((accumulator, statusTab) => {
        accumulator[statusTab.id] = applicationList.filter((application) =>
          matchesStatusTab(application.status, statusTab.id),
        ).length
        return accumulator
      }, {} as StatusTabCounts),
    [applicationList],
  )

  const submittedJob = (location.state as SubmittedJobState | null)?.submittedJob
  const showSubmittedBanner =
    Boolean(submittedJob) &&
    !applicationList.some((application) => application.job_id === submittedJob?.jobId)

  const activeTabLabel = STATUS_TABS.find((statusTab) => statusTab.id === tab)?.label ?? ''
  const total = applicationList.length

  return (
    <div>
      <PageHeader
        title="Opportunities"
        description="Every opportunity you're pursuing, in one place — newest activity first."
      />

      <Card className="mb-8 transition-shadow hover:shadow-md">
        <JobUrlSubmitForm />
      </Card>

      {showSubmittedBanner ? (
        // Deliberately a plain, non-interactive element: it is a placeholder
        // for a row that doesn't exist server-side yet (the Application is
        // created asynchronously off `jobs.discovered`), so there is nothing
        // to click through to. `tests/e2e/helpers.ts` relies on this not
        // being a `role="button"` the way real rows are.
        <div
          role="status"
          className="mb-4 flex items-center gap-2 rounded-lg border border-status-progress bg-status-progress-bg px-4 py-3 text-sm text-status-progress"
        >
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
          <span>
            {submittedJob?.company ?? 'Your job'}
            {submittedJob?.title ? ` — ${submittedJob.title}` : ''} submitted. Analyzing… it appears
            below once matching finishes.
          </span>
        </div>
      ) : null}

      {applications.isLoading ? (
        // Skeletons are `aria-hidden` by design (see components/Skeleton.tsx),
        // so the wrapper carries the announcement.
        <div role="status" aria-label="Loading opportunities" className="flex flex-col gap-2">
          <Skeleton className="h-9 w-72 rounded-lg" />
          <Skeleton className="h-14 rounded-lg" />
          <Skeleton className="h-14 rounded-lg" />
          <Skeleton className="h-14 rounded-lg" />
        </div>
      ) : applications.isError ? (
        // Page-level error with retry: this is the page's primary data
        // surface, so a partial/inline treatment would leave nothing behind
        // it (routes.md's error row for this route).
        <ErrorState message={toApiError(applications.error).message} onRetry={() => applications.refetch()} />
      ) : total === 0 ? (
        // Empty state #1 — the account has nothing in the pipeline at all.
        // No filter is involved, so there is no filter to clear; the only
        // useful next step is the primary action itself.
        isBlockedOnMissingResume ? (
          <EmptyState
            icon={<FileText className="h-8 w-8" aria-hidden />}
            title="Add a resume to get started"
            description="Upload a resume before adding opportunities — matching needs at least one analyzed resume to compare jobs against. Every posting you submit is scored against it."
            action={
              <Link to="/resumes" className={PRIMARY_LINK_CLASSES}>
                Upload a resume
              </Link>
            }
          />
        ) : (
          <EmptyState
            icon={<Briefcase className="h-8 w-8" aria-hidden />}
            title="No opportunities yet"
            description="Paste a job posting URL above to add the first one. Each posting is fetched, matched against your resume, and tracked here automatically."
          />
        )
      ) : (
        <>
          <div className="mb-4">
            <StatusFilterTabs
              value={tab}
              onChange={setTab}
              counts={counts}
              panelId={LIST_REGION_ID}
            />
          </div>

          <div id={LIST_REGION_ID} role="tabpanel" aria-labelledby={tabId(tab)}>
            {filtered.length === 0 ? (
              // Empty state #2 — rows exist, just none under this tab. Reads
              // as a filter result, not as an empty account, and carries the
              // obvious fix.
              <EmptyState
                icon={<SlidersHorizontal className="h-8 w-8" aria-hidden />}
                title="No opportunities in this status"
                description={`None of your ${total} ${total === 1 ? 'opportunity is' : 'opportunities are'} in ${activeTabLabel} right now.`}
                action={
                  <Button type="button" variant="secondary" onClick={() => setTab('all')}>
                    Show all opportunities
                  </Button>
                }
              />
            ) : (
              <OpportunityTable
                applications={filtered}
                onOpen={(row) => navigate(`/opportunities/${row.id}`)}
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}
