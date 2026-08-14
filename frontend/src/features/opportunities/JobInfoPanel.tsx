/**
 * Job Information panel — Opportunity Detail. Renders the full `JobResponse`
 * shape, all fields present as of the Step 10.5 backend fix (no workaround
 * needed) — see docs/frontend/api-mapping.md#backend-gaps-affecting-this-mapping.
 *
 * Independent panel: its own loading/error state, never blocks the rest of
 * the page (see docs/frontend/error-handling.md's panel-scoped row).
 *
 * Owner: frontend-opportunities-agent.
 */

import { Card, ErrorState, Skeleton } from '../../components'
import type { JobResponse } from '../../api/types'

type JobInfoPanelProps = {
  job: JobResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

export function JobInfoPanel({ job, isLoading, isError, errorMessage, onRetry }: JobInfoPanelProps) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Job information</h2>
      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load job details.'} onRetry={onRetry} />
      ) : !job ? null : (
        <dl className="flex flex-col gap-4 text-sm">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium text-gray-500">Company</dt>
              <dd className="text-gray-900">{job.company}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-gray-500">Title</dt>
              <dd className="text-gray-900">{job.title}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-gray-500">Location</dt>
              <dd className="text-gray-900">{job.location ?? 'Not specified'}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-gray-500">Experience required</dt>
              <dd className="text-gray-900">{job.experience_required ?? 'Not specified'}</dd>
            </div>
          </div>

          {job.extracted_skills.length > 0 ? (
            <div>
              <dt className="text-xs font-medium text-gray-500">Extracted skills</dt>
              <dd className="mt-1 flex flex-wrap gap-1.5">
                {job.extracted_skills.map((skill) => (
                  <span key={skill} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                    {skill}
                  </span>
                ))}
              </dd>
            </div>
          ) : null}

          <div>
            <dt className="text-xs font-medium text-gray-500">Description</dt>
            <dd className="mt-1 whitespace-pre-line text-gray-700">{job.description}</dd>
          </div>

          {job.source_url ? (
            <div>
              <dt className="text-xs font-medium text-gray-500">Source</dt>
              <dd className="mt-1">
                <a
                  href={job.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-brand hover:underline"
                >
                  View original posting
                </a>
              </dd>
            </div>
          ) : null}
        </dl>
      )}
    </Card>
  )
}
