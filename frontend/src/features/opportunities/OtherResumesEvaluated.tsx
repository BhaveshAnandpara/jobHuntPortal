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
 * Owner: frontend-opportunities-agent.
 */

import { Card, ErrorState, Skeleton } from '../../components'
import type { ProfileMatchScore, ResumeResponse } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type OtherResumesEvaluatedProps = {
  profileScores: ProfileMatchScore[] | undefined
  resumes: ResumeResponse[] | undefined
  selectedResumeId: string | null | undefined
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
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: OtherResumesEvaluatedProps) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Other resumes evaluated</h2>
      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load resume comparison.'} onRetry={onRetry} />
      ) : !profileScores || profileScores.length === 0 ? (
        <p className="text-sm text-gray-500">No resume comparison available yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {[...profileScores]
            .sort((a, b) => b.score - a.score)
            .map((profileScore) => (
              <li
                key={profileScore.profile_id}
                className="flex items-center justify-between gap-3 rounded-md border border-gray-100 px-3 py-2 text-sm"
              >
                <span className="text-gray-700">
                  {resumeLabel(profileScore, resumes)}
                  {selectedResumeId && profileScore.resume_id === selectedResumeId ? (
                    <span className="ml-2 rounded-full bg-status-positive-bg px-2 py-0.5 text-xs text-status-positive">
                      Selected
                    </span>
                  ) : null}
                </span>
                <span className="font-medium text-gray-900">{formatScorePercent(profileScore.score)}</span>
              </li>
            ))}
        </ul>
      )}
    </Card>
  )
}
