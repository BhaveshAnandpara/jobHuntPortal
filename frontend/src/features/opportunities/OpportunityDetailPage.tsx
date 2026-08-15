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
 * (never modified) and wired against the locked contract:
 * `{ jobId, company?, title?, location? }`. `company`/`title` come from the
 * already-loaded `application`; `location` comes from the separately-loaded
 * `job` (may still be `undefined` while that panel's own query is in
 * flight — harmless, `ContactsPanel` treats it as optional and simply
 * disables its "Search again" action until `company`/`title` are present).
 */

import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { ErrorState, PageHeader, Spinner, StatusBadge } from '../../components'
import { useApplication, useApplicationHistory } from '../../api/tracking'
import { useJob } from '../../api/jobs'
import { useJobMatch } from '../../api/matching'
import { useProfiles } from '../../api/profiles'
import { useOutreachList } from '../../api/outreach'
import { pollUntil } from '../../hooks/usePolling'
import { toApiError } from '../../api/client'
import { ContactsPanel } from '../contacts/ContactsPanel'
import { isApplicationSettled } from './pipeline'
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

  if (!applicationId) {
    return null
  }

  if (applicationQuery.isLoading) {
    return (
      <div>
        <PageHeader title="Opportunity" />
        <Spinner label="Loading opportunity" />
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
    <div className="flex flex-col gap-6">
      <PageHeader
        title={application.title}
        description={application.company}
        action={<StatusBadge status={application.status} />}
      />

      <JobInfoPanel
        job={jobQuery.data}
        isLoading={jobQuery.isLoading}
        isError={jobQuery.isError}
        errorMessage={jobQuery.isError ? toApiError(jobQuery.error).message : undefined}
        onRetry={() => jobQuery.refetch()}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <MatchPanel
          application={application}
          recommendation={jobMatchQuery.data?.recommendation}
          isRecommendationLoading={jobMatchQuery.isLoading}
          isRecommendationError={jobMatchQuery.isError}
          recommendationErrorMessage={jobMatchQuery.isError ? toApiError(jobMatchQuery.error).message : undefined}
          onRetryRecommendation={() => jobMatchQuery.refetch()}
          selectedProfile={selectedProfile}
          isProfileLoading={profilesQuery.isLoading}
          isProfileError={profilesQuery.isError}
          profileErrorMessage={profilesQuery.isError ? toApiError(profilesQuery.error).message : undefined}
          onRetryProfile={() => profilesQuery.refetch()}
        />

        <OtherResumesEvaluated
          profileScores={jobMatchQuery.data?.profile_scores}
          profiles={profilesQuery.data}
          selectedResumeId={application.selected_resume_id}
          isLoading={jobMatchQuery.isLoading}
          isError={jobMatchQuery.isError}
          errorMessage={jobMatchQuery.isError ? toApiError(jobMatchQuery.error).message : undefined}
          onRetry={() => jobMatchQuery.refetch()}
        />
      </div>

      <ContactsPanel
        jobId={application.job_id}
        company={application.company}
        title={application.title}
        location={jobQuery.data?.location ?? undefined}
      />

      <OutreachSummaryPanel
        outreach={outreachForJob}
        isLoading={outreachQuery.isLoading}
        isError={outreachQuery.isError}
        errorMessage={outreachQuery.isError ? toApiError(outreachQuery.error).message : undefined}
        onRetry={() => outreachQuery.refetch()}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <LifecycleTimeline
          history={historyQuery.data}
          isLoading={historyQuery.isLoading}
          isError={historyQuery.isError}
          errorMessage={historyQuery.isError ? toApiError(historyQuery.error).message : undefined}
          onRetry={() => historyQuery.refetch()}
        />

        <StatusActionMenu
          applicationId={application.id}
          currentStatus={application.status}
          onSettled={() => applicationQuery.refetch()}
        />
      </div>
    </div>
  )
}
