/**
 * Match panel — Opportunity Detail. Score/matched/missing skills are read
 * from the already-fetched `ApplicationResponse` (never re-derived, never
 * duplicated from `useJobMatch`) per docs/frontend/api-mapping.md's explicit
 * guidance; `recommendation` is joined by `reasoning`, `hard_blocker` and
 * `insufficient_detail` as the fields only `useJobMatch` carries — the
 * backend projects those three onto `JobMatchResponse` from the selected
 * profile's `ProfileMatchScore`, so they arrive already resolved to the
 * winning resume and this panel never touches the `profile_scores` array.
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
 *   it. (`hard_blocker` and `insufficient_detail` are not a second
 *   confidence figure — they are the backend's own stated findings, shown
 *   as a warning and a footnote rather than as a number beside the score);
 * - strengths and gaps keep the status-category colors (green/red) from the
 *   shared token set rather than one-off hexes, and each says so in words as
 *   well as color.
 *
 * Owner: frontend-opportunities-agent.
 */

import { AlertTriangle, Info, Sparkles } from 'lucide-react'
import { ErrorState, Skeleton, StatusBadge } from '../../components'
import { DetailPanel, StageNotReached } from './DetailPanel'
import type { ApplicationResponse, MatchRecommendation, ResumeProfile } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type MatchPanelProps = {
  application: ApplicationResponse
  /** False only when the matching stage provably hasn't run yet — see lifecycleStage.ts. */
  isAvailable: boolean
  recommendation: MatchRecommendation | undefined
  /**
   * The three explanation fields below come from the same `useJobMatch`
   * query as `recommendation` — the backend projects them onto
   * `JobMatchResponse` from the *selected* profile's `ProfileMatchScore`.
   * They are not on `ApplicationResponse` and must not be re-derived from
   * `profile_scores` here.
   *
   * All three are absent for any match computed before the feature existed,
   * so every one of `undefined` / `null` / `''` / `false` means "not
   * present" and renders nothing at all — never an empty box.
   */
  reasoning?: string | null
  hardBlocker?: string | null
  insufficientDetail?: boolean
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
  reasoning,
  hardBlocker,
  insufficientDetail,
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

  // Trimmed so a whitespace-only string from an older record is treated as
  // absent rather than rendering an empty paragraph.
  const reasoningText = reasoning?.trim()
  const hardBlockerText = hardBlocker?.trim()

  return (
    <DetailPanel title="Match" description="How your selected resume scored against this posting.">
      <div className="flex flex-col gap-5">
        {hardBlockerText ? (
          // Deliberately the first thing in the panel and the loudest thing
          // in it: this is the one finding that can make the rest of the
          // score moot, so it has to land before the user reads a strong
          // percentage and invests effort on the back of it.
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-status-negative/30 bg-status-negative-bg p-3"
          >
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-status-negative"
              aria-hidden
            />
            <div>
              <p className="text-sm font-semibold text-status-negative">
                Likely blocker
              </p>
              <p className="mt-0.5 text-sm text-status-negative">{hardBlockerText}</p>
            </div>
          </div>
        ) : null}

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

        {insufficientDetail ? (
          // Not an error and not styled as one: the score is still real,
          // it just rests on a thin posting. Sits under the score it
          // qualifies rather than at the top, so it reads as a footnote to
          // the number instead of competing with the blocker above.
          <p className="-mt-2 flex items-start gap-1.5 text-xs text-gray-500">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>Limited job detail — this score may be less reliable.</span>
          </p>
        ) : null}

        {reasoningText ? (
          <div>
            <p className="text-xs font-medium text-gray-500">Why this score</p>
            <p className="mt-1.5 text-sm leading-relaxed text-gray-700">{reasoningText}</p>
          </div>
        ) : null}

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
