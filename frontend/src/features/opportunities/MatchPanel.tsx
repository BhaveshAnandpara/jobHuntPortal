/**
 * Match panel — Opportunity Detail. Score/matched/missing skills are read
 * from the already-fetched `ApplicationResponse` (never re-derived, never
 * duplicated from `useJobMatch`) per docs/frontend/api-mapping.md's explicit
 * guidance; `recommendation` is the one field only `useJobMatch` carries.
 * Selected resume resolves `ApplicationResponse.selected_resume_id` (a
 * `resume_id`) against `ResumeProfile.resume_id` — see
 * OpportunityDetailPage.tsx's header comment for why `useProfiles` +
 * client-side match was used instead of `useProfile(profileId)` directly.
 *
 * Independent loading/error state per sub-query it depends on (recommendation,
 * selected resume) — matches score/skills always render immediately since
 * they come from the already-loaded `application`.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Card, ErrorState, Skeleton, StatusBadge } from '../../components'
import type { ApplicationResponse, MatchRecommendation, ResumeProfile } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type MatchPanelProps = {
  application: ApplicationResponse
  recommendation: MatchRecommendation | undefined
  isRecommendationLoading: boolean
  isRecommendationError: boolean
  recommendationErrorMessage?: string
  onRetryRecommendation: () => void
  selectedProfile: ResumeProfile | undefined
  isProfileLoading: boolean
  isProfileError: boolean
  profileErrorMessage?: string
  onRetryProfile: () => void
}

export function MatchPanel({
  application,
  recommendation,
  isRecommendationLoading,
  isRecommendationError,
  recommendationErrorMessage,
  onRetryRecommendation,
  selectedProfile,
  isProfileLoading,
  isProfileError,
  profileErrorMessage,
  onRetryProfile,
}: MatchPanelProps) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Match</h2>

      <div className="flex flex-col gap-4 text-sm">
        <div className="flex items-start gap-6">
          <div>
            <p className="text-xs font-medium text-gray-500">Score</p>
            <p className="text-lg font-semibold text-gray-900">
              {application.match_score != null ? formatScorePercent(application.match_score) : 'Not yet analyzed'}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-gray-500">Recommendation</p>
            {isRecommendationLoading ? (
              <Skeleton className="h-5 w-20" />
            ) : isRecommendationError ? (
              <span className="flex items-center gap-2">
                <span className="text-xs text-status-negative">
                  {recommendationErrorMessage ?? 'Could not load recommendation.'}
                </span>
                <button
                  type="button"
                  onClick={onRetryRecommendation}
                  className="text-xs font-medium text-status-negative underline"
                >
                  Retry
                </button>
              </span>
            ) : recommendation ? (
              <StatusBadge status={recommendation} />
            ) : (
              <span className="text-gray-500">Not yet analyzed</span>
            )}
          </div>
        </div>

        <div>
          <p className="text-xs font-medium text-gray-500">Selected resume</p>
          {isProfileLoading ? (
            <Skeleton className="mt-1 h-5 w-32" />
          ) : isProfileError ? (
            <ErrorState
              message={profileErrorMessage ?? 'Could not load the selected resume.'}
              onRetry={onRetryProfile}
            />
          ) : selectedProfile ? (
            <p className="text-gray-900">{selectedProfile.title}</p>
          ) : (
            <span className="text-gray-500">No resume selected yet</span>
          )}
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <p className="text-xs font-medium text-gray-500">Strengths</p>
            {application.matched_skills.length > 0 ? (
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {application.matched_skills.map((skill) => (
                  <li
                    key={skill}
                    className="rounded-full bg-status-positive-bg px-2 py-0.5 text-xs text-status-positive"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500">None recorded</p>
            )}
          </div>
          <div>
            <p className="text-xs font-medium text-gray-500">Gaps</p>
            {application.missing_skills.length > 0 ? (
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {application.missing_skills.map((skill) => (
                  <li
                    key={skill}
                    className="rounded-full bg-status-negative-bg px-2 py-0.5 text-xs text-status-negative"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500">None recorded</p>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}
