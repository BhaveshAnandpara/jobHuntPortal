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
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. Same three data
 * sources, no new ones:
 * - before the matching stage has run (`isAvailable === false`, decided by
 *   lifecycleStage.ts) the panel says matching hasn't started rather than
 *   showing a skeleton or a 0% score — a spinner here would claim a request
 *   is in flight when the real answer is "this step is still queued";
 * - the score gets a bar, because `match_score` is a real 0–1 number the
 *   backend already computed. There is no second "confidence" figure beside
 *   it: `match_score` and `recommendation` are the only two judgments the API
 *   returns, so they are the only two shown (spec Section 3, item 1);
 * - strengths and gaps keep the status-category colors (green/red) from the
 *   shared token set rather than one-off hexes, and each says so in words as
 *   well as color.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Sparkles } from 'lucide-react'
import { ErrorState, Skeleton, StatusBadge } from '../../components'
import { DetailPanel, StageNotReached } from './DetailPanel'
import type { ApplicationResponse, MatchRecommendation, ResumeProfile } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type MatchPanelProps = {
  application: ApplicationResponse
  /** False only when the matching stage provably hasn't run yet — see lifecycleStage.ts. */
  isAvailable: boolean
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

function SkillList({
  label,
  skills,
  tone,
  emptyCopy,
}: {
  label: string
  skills: string[]
  tone: 'positive' | 'negative'
  emptyCopy: string
}) {
  const toneClasses =
    tone === 'positive'
      ? 'bg-status-positive-bg text-status-positive'
      : 'bg-status-negative-bg text-status-negative'

  return (
    <div>
      <p className="text-xs font-medium text-gray-500">{label}</p>
      {skills.length > 0 ? (
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {skills.map((skill) => (
            <li key={skill} className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${toneClasses}`}>
              {skill}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-sm text-gray-400">{emptyCopy}</p>
      )}
    </div>
  )
}

export function MatchPanel({
  application,
  isAvailable,
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
  if (!isAvailable) {
    return (
      <DetailPanel title="Match">
        <StageNotReached
          icon={<Sparkles className="h-5 w-5" aria-hidden />}
          title="Not matched yet"
          description="This posting is still being normalized. Matching runs automatically straight after, and the score, recommendation and skill breakdown appear here."
        />
      </DetailPanel>
    )
  }

  const scorePercent =
    application.match_score != null ? Math.max(0, Math.min(100, application.match_score * 100)) : null

  return (
    <DetailPanel title="Match" description="How your selected resume scored against this posting.">
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
          <div className="min-w-[9rem]">
            <p className="text-xs font-medium text-gray-500">Score</p>
            {scorePercent != null ? (
              <>
                <p className="text-2xl font-semibold tabular-nums text-gray-900">
                  {formatScorePercent(application.match_score as number)}
                </p>
                <div
                  className="mt-1.5 h-1.5 w-36 overflow-hidden rounded-full bg-gray-200"
                  role="img"
                  aria-label={`Match score ${formatScorePercent(application.match_score as number)}`}
                >
                  <div className="h-full rounded-full bg-brand" style={{ width: `${scorePercent}%` }} />
                </div>
              </>
            ) : (
              // Reached the matching stage but no score on the record yet —
              // a real in-between, distinct from "matching hasn't started".
              <p className="mt-0.5 text-sm text-gray-400">No score recorded yet</p>
            )}
          </div>

          <div>
            <p className="text-xs font-medium text-gray-500">Recommendation</p>
            <div className="mt-1.5">
              {isRecommendationLoading ? (
                <span role="status" aria-label="Loading recommendation">
                  <Skeleton className="h-5 w-24 rounded-full" />
                </span>
              ) : isRecommendationError ? (
                // Scoped tighter than a whole-panel ErrorState on purpose:
                // only the recommendation comes from `GET /jobs/{id}/matches`,
                // so its failure must not hide the score and skills that came
                // from the already-loaded application.
                <span role="alert" className="flex flex-wrap items-center gap-2">
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
                <span className="text-sm text-gray-400">Not yet analyzed</span>
              )}
            </div>
          </div>
        </div>

        <div>
          <p className="text-xs font-medium text-gray-500">Selected resume</p>
          <div className="mt-1.5">
            {isProfileLoading ? (
              <span role="status" aria-label="Loading selected resume">
                <Skeleton className="h-5 w-40 rounded-md" />
              </span>
            ) : isProfileError ? (
              <ErrorState
                message={profileErrorMessage ?? 'Could not load the selected resume.'}
                onRetry={onRetryProfile}
              />
            ) : selectedProfile ? (
              <p className="text-sm text-gray-900">{selectedProfile.title}</p>
            ) : (
              <p className="text-sm text-gray-400">No resume selected yet</p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SkillList
            label="Strengths"
            skills={application.matched_skills}
            tone="positive"
            emptyCopy="No overlapping skills were recorded."
          />
          <SkillList
            label="Gaps"
            skills={application.missing_skills}
            tone="negative"
            emptyCopy="No missing skills were recorded."
          />
        </div>
      </div>
    </DetailPanel>
  )
}
