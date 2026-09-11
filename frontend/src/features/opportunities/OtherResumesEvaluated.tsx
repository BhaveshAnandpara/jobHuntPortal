/**
 * "Other resumes evaluated" — Opportunity Detail. Renders every entry of
 * `JobMatchResponse.profile_scores`, not just the winning resume — this was
 * a previously-blocked feature (missing `profile_scores` on the wire),
 * unblocked by the Step 10.5 backend fix; see
 * docs/frontend/api-mapping.md#backend-gaps-affecting-this-mapping. Never
 * computed client-side, rendered exactly as received.
 *
 * `ProfileMatchScore` carries only `profile_id`/`resume_id` (no human name),
 * so this cross-references the already-fetched `resumes` list (`GET
 * /resumes`) to show each entry's uploaded file name, falling back to a
 * generic label if a lookup hasn't resolved for some reason. Deliberately
 * not the resume's extracted `title` (from `/profiles`) — several resumes
 * from the same candidate often extract to the same title (e.g. "Associate
 * Software Engineer" for every variant), making entries indistinguishable;
 * the file name is what's actually unique per upload.
 *
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. Same one query, same
 * rendering-as-received rule:
 * - before matching has run, this says so rather than showing an empty list
 *   or a skeleton (the comparison is a by-product of matching, so it has the
 *   same gate as `MatchPanel` — see lifecycleStage.ts);
 * - each row now carries the score as a bar plus the number, so the ranking
 *   is readable at a glance without reading four percentages;
 * - the winning row is marked once, by the same "Selected" pill as before —
 *   the backend's choice, not a client-side re-pick.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Layers } from 'lucide-react'
import { ErrorState } from '../../components'
import { DetailPanel, PanelLoading, StageNotReached } from './DetailPanel'
import type { ProfileMatchScore, ResumeResponse } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type OtherResumesEvaluatedProps = {
  profileScores: ProfileMatchScore[] | undefined
  resumes: ResumeResponse[] | undefined
  selectedResumeId: string | null | undefined
  /** False only when the matching stage provably hasn't run yet — see lifecycleStage.ts. */
  isAvailable: boolean
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

function resumeLabel(profileScore: ProfileMatchScore, resumes: ResumeResponse[] | undefined): string {
  return resumes?.find((resume) => resume.id === profileScore.resume_id)?.file_name ?? 'Resume'
}

export function OtherResumesEvaluated({
  profileScores,
  resumes,
  selectedResumeId,
  isAvailable,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: OtherResumesEvaluatedProps) {
  return (
    <DetailPanel
      title="Other resumes evaluated"
      description="Every resume this posting was scored against, best first."
    >
      {!isAvailable ? (
        <StageNotReached
          icon={<Layers className="h-5 w-5" aria-hidden />}
          title="No comparison yet"
          description="Matching scores each of your resumes against the posting. The full comparison appears here once it runs."
        />
      ) : isLoading ? (
        <PanelLoading label="Loading resume comparison" lines={2} />
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load resume comparison.'} onRetry={onRetry} />
      ) : !profileScores || profileScores.length === 0 ? (
        <p className="text-sm text-gray-400">
          Matching recorded no per-resume scores for this posting.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {[...profileScores]
            .sort((a, b) => b.score - a.score)
            .map((profileScore) => {
              const isSelected = Boolean(selectedResumeId) && profileScore.resume_id === selectedResumeId
              const percent = Math.max(0, Math.min(100, profileScore.score * 100))
              return (
                <li
                  key={profileScore.profile_id}
                  className={`rounded-md border px-3 py-2.5 ${
                    isSelected ? 'border-status-positive/40 bg-status-positive-bg' : 'border-gray-200'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm text-gray-700">
                        {resumeLabel(profileScore, resumes)}
                      </span>
                      {isSelected ? (
                        <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-xs font-medium text-status-positive">
                          Selected
                        </span>
                      ) : null}
                    </span>
                    <span className="shrink-0 text-sm font-medium tabular-nums text-gray-900">
                      {formatScorePercent(profileScore.score)}
                    </span>
                  </div>
                  <div className="mt-2 h-1 overflow-hidden rounded-full bg-gray-200" aria-hidden>
                    <div
                      className={`h-full rounded-full ${isSelected ? 'bg-status-positive' : 'bg-gray-400'}`}
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                </li>
              )
            })}
        </ul>
      )}
    </DetailPanel>
  )
}
