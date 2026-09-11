/**
 * Route: / — see docs/frontend/routes.md#--dashboard.
 * Owner: frontend-opportunities-agent (primary-action + pipeline-summary
 * sections). The "needs attention" list reading both applications and
 * outreach is cross-feature glue owned by frontend-integration-ui-agent —
 * see docs/frontend/agent-ownership.md's integration-ui-agent entry; this
 * page reads `useOutreachList('PENDING_APPROVAL')` directly per its
 * own agent-ownership.md entry ("reading outreach data for a summary
 * display is fine; you're only forbidden from implementing outreach
 * *actions*"), which is enough for the count/deep-link list in
 * `NeedsAttentionList` without needing the richer cross-feature component
 * integration-ui-agent may add later.
 *
 * Polling: `GET /applications` and `GET /outreach?status=PENDING_APPROVAL`
 * both poll at 10s, always-on — the slowest interval of any page, per
 * docs/frontend/async-workflows.md's table (Dashboard is a summary view,
 * not the primary place a user watches active progress).
 *
 * T5 (docs/frontend/frontend-revamp-spec.md) restyle. No request or response
 * handling changed; only presentation and which states are reachable:
 * - The page is now three explicit slots — primary action, what needs a
 *   decision, then what's in flight — instead of four same-weight cards.
 * - **Brand-new account:** when `GET /profiles` resolves to zero active
 *   profiles (see `jobSubmissionReadiness.ts` for why that, and not
 *   `GET /resumes`, is the real gate), the primary-action slot becomes an
 *   onboarding `EmptyState` pointing at `/resumes`. With no opportunities
 *   either, the pipeline/recent sections are omitted entirely rather than
 *   rendering three zeros at someone who has nothing to see yet — the
 *   explicit requirement in T5.
 * - Per-query states are now separated: the pipeline summary skeletons/errors
 *   on `GET /applications`, the review list on `GET /outreach`, so a failure
 *   in one never blanks the other (error-handling.md's secondary-panel row).
 * - Needs-attention rows deep link to `/outreach/:outreachId` instead of
 *   dumping the user at the top of the queue.
 * - Submitting still navigates to `/opportunities`, never to a detail page:
 *   the Application row is created asynchronously off `jobs.discovered`, so
 *   there is no detail route to land on yet (user-flows.md#job-submission-flow).
 * - Removed a stray `console.log` of the recent-opportunities array, left
 *   over from earlier development (noted as known cleanup in the spec's
 *   Section 1).
 */

import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { Card, EmptyState, PageHeader } from '../../components'
import { useApplications } from '../../api/tracking'
import { useOutreachList } from '../../api/outreach'
import { pollAlways } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { JobUrlSubmitForm } from './JobUrlSubmitForm'
import { NeedsAttentionList } from './NeedsAttentionList'
import { PipelineSummary } from './PipelineSummary'
import { RecentOpportunities } from './RecentOpportunities'
import { useJobSubmissionReadiness } from './jobSubmissionReadiness'
import { matchesStatusTab } from './pipeline'

const RECENT_COUNT = 5

/**
 * The onboarding CTA has to be a real `<a>` (it navigates), and the shared
 * `Button` primitive always renders a `<button>` — adding an `asChild`/`href`
 * escape hatch to it would be a change to `src/components`, outside this
 * ticket's file scope. So the primary-button treatment is mirrored here from
 * the same `@theme` tokens `Button` uses (`bg-brand`/`hover:bg-brand-hover`),
 * never a one-off color.
 */
const PRIMARY_LINK_CLASSES =
  'inline-flex items-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-hover focus-visible:ring-2 focus-visible:ring-ring'

export function DashboardPage() {
  const applications = useApplications(undefined, {
    refetchInterval: pollAlways(10000),
  })
  const pendingOutreach = useOutreachList('PENDING_APPROVAL', {
    refetchInterval: pollAlways(10000),
  })
  const { isBlockedOnMissingResume } = useJobSubmissionReadiness()

  const applicationList = useMemo(() => applications.data ?? [], [applications.data])
  const hasAnyOpportunities = applicationList.length > 0

  const recent = useMemo(
    () =>
      [...applicationList]
        .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
        .slice(0, RECENT_COUNT),
    [applicationList],
  )

  // `OutreachResponse` has a `job_id` but no company/title; `ApplicationResponse`
  // has both, keyed by the same `job_id`. One map, built from data already on
  // screen, is what lets a review row name its job instead of showing a bare
  // message excerpt.
  const applicationsByJobId = useMemo(() => {
    const map = new Map<string, (typeof applicationList)[number]>()
    for (const application of applicationList) {
      map.set(application.job_id, application)
    }
    return map
  }, [applicationList])

  const counts = {
    active: applicationList.filter((a) => matchesStatusTab(a.status, 'active')).length,
    applied: applicationList.filter((a) => matchesStatusTab(a.status, 'applied')).length,
    closed: applicationList.filter((a) => matchesStatusTab(a.status, 'closed')).length,
  }

  // Nothing to show at all: no resume to match against *and* no history.
  // Showing a pipeline of three zeros here would be technically accurate and
  // completely useless — T5 asks for the resume CTA instead.
  const showOnlyOnboarding = isBlockedOnMissingResume && !hasAnyOpportunities

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Submit a job posting, review anything waiting on your approval, and see where every opportunity stands."
      />

      {isBlockedOnMissingResume ? (
        <div className="mb-8">
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
        </div>
      ) : (
        <Card className="mb-8 transition-shadow hover:shadow-md">
          <JobUrlSubmitForm />
        </Card>
      )}

      {showOnlyOnboarding ? null : (
        <>
          <NeedsAttentionList
            items={pendingOutreach.data ?? []}
            applicationsByJobId={applicationsByJobId}
            isLoading={pendingOutreach.isLoading}
            errorMessage={
              pendingOutreach.isError ? toApiError(pendingOutreach.error).message : undefined
            }
            onRetry={() => void pendingOutreach.refetch()}
          />

          <PipelineSummary
            counts={counts}
            isLoading={applications.isLoading}
            errorMessage={applications.isError ? toApiError(applications.error).message : undefined}
            onRetry={() => void applications.refetch()}
            hasAnyOpportunities={hasAnyOpportunities}
          />

          {hasAnyOpportunities ? <RecentOpportunities applications={recent} /> : null}
        </>
      )}
    </div>
  )
}
