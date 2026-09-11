/**
 * Route: /opportunities/:applicationId — see
 * docs/frontend/routes.md#opportunitiesapplicationid--opportunity-detail.
 * Owner: frontend-opportunities-agent.
 *
 * Route param is `applicationId` (NOT `jobId`) — `GET /applications/{id}`
 * is the primary payload, and `application.job_id` drives every secondary
 * call. Every one of the ~7 API calls below is an independent query: one
 * failing panel (e.g. contacts) must never take down panels that loaded
 * fine (e.g. job info) — see docs/frontend/error-handling.md's
 * panel-scoped row.
 *
 * Polling: `GET /applications/{id}` polls at 3s until this application's
 * status is terminal or `OUTREACH_SENT` (see pipeline.ts's
 * `isApplicationSettled`) — everything else on this page (job detail,
 * match, profiles, outreach list, history) is fetch-once +
 * `refetchOnWindowFocus`, per docs/frontend/async-workflows.md's table.
 *
 * Selected Resume id resolution: `ApplicationResponse.selected_resume_id`
 * is a `resume_id`, but `useProfile(profileId)` is keyed by `profile_id` —
 * distinct fields on `ResumeProfile` (confirmed against
 * src/api/generated/schema.d.ts). There is no "get profile by resume id"
 * endpoint, so this page uses `useProfiles()` and matches
 * client-side by `resume_id` instead of calling `useProfile` directly, per
 * the documented judgment call in this agent's brief.
 *
 * `ContactsPanel` is imported from frontend-contacts-agent's feature folder
 * and wired against its contract: `{ jobId, company?, title?, location?,
 * searchStarted? }`. `company`/`title` come from the already-loaded
 * `application`; `location` comes from the separately-loaded `job` (may
 * still be `undefined` while that panel's own query is in flight —
 * harmless, `ContactsPanel` treats it as optional and simply disables its
 * "Search again" action until `company`/`title` are present).
 *
 * ---------------------------------------------------------------------------
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. **No request or
 * response handling changed** — same seven queries, same 3s stop condition,
 * same client-side `job_id` filter over the unfiltered `GET /outreach` (the
 * documented backend gap this page works around). What changed:
 *
 * 1. **Progressive panel unlocking.** Each stage-dependent panel now asks
 *    `lifecycleStage.ts` whether its stage has actually run, and renders a
 *    "hasn't started" placeholder when it provably hasn't. That placeholder
 *    is visually distinct from a skeleton (a request is in flight) and from
 *    an empty state (the stage ran and found nothing) — the spec calls these
 *    three out as separate states, and on this page all three are reachable
 *    for the same panel at different times. The question is answered from
 *    real fields only (status, history, `match_score`), and when it can't be
 *    answered the panel renders its ordinary data view rather than asserting
 *    a negative — see that module's header.
 *
 * 2. **Two-column desktop, stacked mobile**, per
 *    design-system.md#responsive-behavior. The wide column carries the
 *    opportunity's substance (job, match, resume comparison, contacts); the
 *    narrow column carries the things you act on or scan (status control,
 *    outreach state, history). Below `lg` the grid collapses and the columns
 *    concatenate in that order, so the phone reading order is still
 *    job → match → contacts → actions.
 *
 * 3. **Independent panel errors, verified rather than implied.** Each
 *    secondary query's `isError` is passed only to the panel that query
 *    feeds, and nothing on this page early-returns on a secondary failure —
 *    the only early return is for `GET /applications/{id}` itself, which is
 *    the page's primary payload (error-handling.md's page-level row).
 *    `OpportunityDetailPage.test.tsx` asserts this per panel by failing one
 *    query at a time and checking the other panels still render.
 *
 * 4. **Status transitions.** The rejection this page's `PATCH` produces is
 *    `400 VALIDATION_ERROR`, not `409` — see `StatusActionMenu.tsx`.
 */

import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { ErrorState, PageHeader, Skeleton, StatusBadge } from '../../components'
import { useApplication, useApplicationHistory } from '../../api/tracking'
import { useJob } from '../../api/jobs'
import { useJobMatch } from '../../api/matching'
import { useProfiles } from '../../api/profiles'
import { useResumes } from '../../api/resumes'
import { useOutreachList } from '../../api/outreach'
import { pollUntil } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { ContactsPanel } from '../contacts/ContactsPanel'
import { isApplicationSettled } from './pipeline'
import { getStageAvailability, shouldRenderStageData } from './lifecycleStage'
import { JobInfoPanel } from './JobInfoPanel'
import { MatchPanel } from './MatchPanel'
import { OtherResumesEvaluated } from './OtherResumesEvaluated'
import { LifecycleTimeline } from './LifecycleTimeline'
import { StatusActionMenu } from './StatusActionMenu'
import { OutreachSummaryPanel } from './OutreachSummaryPanel'

export function OpportunityDetailPage() {
  const { applicationId } = useParams<{ applicationId: string }>()

  const applicationQuery = useApplication(applicationId ?? '', {
    refetchInterval: pollUntil(3000, isApplicationSettled),
  })
  const application = applicationQuery.data

  const historyQuery = useApplicationHistory(applicationId ?? '')
  const jobQuery = useJob(application?.job_id ?? '')
  const jobMatchQuery = useJobMatch(application?.job_id ?? '')
  const profilesQuery = useProfiles()
  const resumesQuery = useResumes()
  const outreachQuery = useOutreachList()

  const selectedProfile = useMemo(
    () => profilesQuery.data?.find((profile) => profile.resume_id === application?.selected_resume_id),
    [profilesQuery.data, application?.selected_resume_id],
  )

  const outreachForJob = useMemo(() => {
    if (!application || !outreachQuery.data) {
      return undefined
    }
    return outreachQuery.data
      .filter((item) => item.job_id === application.job_id)
      .sort((a, b) => new Date(b.generated_at).getTime() - new Date(a.generated_at).getTime())[0]
  }, [application, outreachQuery.data])

  // Progressive unlocking. History is a secondary query and can fail on its
  // own; `getStageAvailability` degrades to status + `match_score` when it
  // has no history to read, so a failed history query never turns a populated
  // panel into a "hasn't started" one.
  const matchAvailability = getStageAvailability('match', application, historyQuery.data)
  const contactsAvailability = getStageAvailability('contacts', application, historyQuery.data)
  const outreachAvailability = getStageAvailability('outreach', application, historyQuery.data)

  if (!applicationId) {
    return null
  }

  if (applicationQuery.isLoading) {
    return (
      <div>
        <PageHeader title="Opportunity" />
        <div
          role="status"
          aria-label="Loading opportunity"
          className="grid grid-cols-1 gap-6 lg:grid-cols-3"
        >
          <div className="flex flex-col gap-6 lg:col-span-2">
            <Skeleton className="h-64 rounded-lg" />
            <Skeleton className="h-48 rounded-lg" />
          </div>
          <div className="flex flex-col gap-6">
            <Skeleton className="h-40 rounded-lg" />
            <Skeleton className="h-40 rounded-lg" />
          </div>
        </div>
      </div>
    )
  }

  if (applicationQuery.isError) {
    const error = toApiError(applicationQuery.error)
    return (
      <div>
        <PageHeader title="Opportunity" />
        <ErrorState
          message={error.status === 404 ? 'This opportunity could not be found.' : error.message}
          onRetry={() => applicationQuery.refetch()}
        />
      </div>
    )
  }

  if (!application) {
    return null
  }

  return (
    <div>
      <Link
        to="/opportunities"
        className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-900"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        All opportunities
      </Link>

      <PageHeader
        title={application.title}
        description={application.company}
        action={<StatusBadge status={application.status} />}
      />

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
        <div className="flex flex-col gap-6 lg:col-span-2">
          <JobInfoPanel
            job={jobQuery.data}
            isLoading={jobQuery.isLoading}
            isError={jobQuery.isError}
            errorMessage={jobQuery.isError ? toApiError(jobQuery.error).message : undefined}
            onRetry={() => jobQuery.refetch()}
          />

          <MatchPanel
            application={application}
            isAvailable={shouldRenderStageData(matchAvailability)}
            recommendation={jobMatchQuery.data?.recommendation}
            isRecommendationLoading={jobMatchQuery.isLoading}
            isRecommendationError={jobMatchQuery.isError}
            recommendationErrorMessage={
              jobMatchQuery.isError ? toApiError(jobMatchQuery.error).message : undefined
            }
            onRetryRecommendation={() => jobMatchQuery.refetch()}
            selectedProfile={selectedProfile}
            isProfileLoading={profilesQuery.isLoading}
            isProfileError={profilesQuery.isError}
            profileErrorMessage={
              profilesQuery.isError ? toApiError(profilesQuery.error).message : undefined
            }
            onRetryProfile={() => profilesQuery.refetch()}
          />

          <OtherResumesEvaluated
            profileScores={jobMatchQuery.data?.profile_scores}
            resumes={resumesQuery.data}
            selectedResumeId={application.selected_resume_id}
            isAvailable={shouldRenderStageData(matchAvailability)}
            isLoading={jobMatchQuery.isLoading}
            isError={jobMatchQuery.isError}
            errorMessage={jobMatchQuery.isError ? toApiError(jobMatchQuery.error).message : undefined}
            onRetry={() => jobMatchQuery.refetch()}
          />

          <ContactsPanel
            jobId={application.job_id}
            company={application.company}
            title={application.title}
            location={jobQuery.data?.location ?? undefined}
            searchStarted={shouldRenderStageData(contactsAvailability)}
          />
        </div>

        <div className="flex flex-col gap-6">
          <StatusActionMenu
            applicationId={application.id}
            currentStatus={application.status}
            onSettled={() => applicationQuery.refetch()}
          />

          <OutreachSummaryPanel
            outreach={outreachForJob}
            isAvailable={shouldRenderStageData(outreachAvailability)}
            isLoading={outreachQuery.isLoading}
            isError={outreachQuery.isError}
            errorMessage={outreachQuery.isError ? toApiError(outreachQuery.error).message : undefined}
            onRetry={() => outreachQuery.refetch()}
          />

          <LifecycleTimeline
            history={historyQuery.data}
            isLoading={historyQuery.isLoading}
            isError={historyQuery.isError}
            errorMessage={historyQuery.isError ? toApiError(historyQuery.error).message : undefined}
            onRetry={() => historyQuery.refetch()}
          />
        </div>
      </div>
    </div>
  )
}
