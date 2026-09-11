/**
 * Job Information panel — Opportunity Detail. Renders the full `JobResponse`
 * shape, all fields present as of the Step 10.5 backend fix (no workaround
 * needed) — see docs/frontend/api-mapping.md#backend-gaps-affecting-this-mapping.
 *
 * Independent panel: its own loading/error state, never blocks the rest of
 * the page (see docs/frontend/error-handling.md's panel-scoped row).
 *
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. Same single query, same
 * fields, no new ones:
 * - the four scalar facts sit in a compact definition grid at the top, so the
 *   description (the long field) isn't competing with them for the first
 *   line of the panel;
 * - a genuinely absent optional field says "Not specified" rather than
 *   rendering a bare dash, matching how T7's table treats an unscored row;
 * - the description is clamped with a real expand control instead of being
 *   allowed to push every panel below it off-screen — the full text is always
 *   one click away and never truncated in the DOM.
 *
 * Owner: frontend-opportunities-agent.
 */

import { useId, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { ErrorState } from '../../components'
import { DetailPanel, PanelLoading } from './DetailPanel'
import type { JobResponse } from '../../api/types'

type JobInfoPanelProps = {
  job: JobResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage?: string
  onRetry: () => void
}

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className={value ? 'text-sm text-gray-900' : 'text-sm text-gray-400'}>
        {value || 'Not specified'}
      </dd>
    </div>
  )
}

export function JobInfoPanel({ job, isLoading, isError, errorMessage, onRetry }: JobInfoPanelProps) {
  const [isDescriptionExpanded, setIsDescriptionExpanded] = useState(false)
  const descriptionId = useId()

  return (
    <DetailPanel title="Job information" description="The posting as it was fetched and normalized.">
      {isLoading ? (
        <PanelLoading label="Loading job information" lines={4} />
      ) : isError ? (
        <ErrorState message={errorMessage ?? 'Could not load job details.'} onRetry={onRetry} />
      ) : !job ? null : (
        <dl className="flex flex-col gap-5">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Fact label="Company" value={job.company} />
            <Fact label="Title" value={job.title} />
            <Fact label="Location" value={job.location} />
            <Fact label="Experience required" value={job.experience_required} />
          </div>

          <div>
            <dt className="text-xs font-medium text-gray-500">Extracted skills</dt>
            <dd className="mt-1.5">
              {job.extracted_skills.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {job.extracted_skills.map((skill) => (
                    <li
                      key={skill}
                      className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700"
                    >
                      {skill}
                    </li>
                  ))}
                </ul>
              ) : (
                // A normalized posting with no extracted skills is a real
                // outcome of ingestion, not missing data — say which.
                <p className="text-sm text-gray-400">No skills were extracted from this posting.</p>
              )}
            </dd>
          </div>

          <div>
            <dt className="text-xs font-medium text-gray-500">Description</dt>
            <dd
              id={descriptionId}
              className={`mt-1 text-sm whitespace-pre-line text-gray-700 ${
                isDescriptionExpanded ? '' : 'line-clamp-6'
              }`}
            >
              {job.description}
            </dd>
            <button
              type="button"
              onClick={() => setIsDescriptionExpanded((expanded) => !expanded)}
              aria-expanded={isDescriptionExpanded}
              aria-controls={descriptionId}
              className="mt-1.5 text-xs font-medium text-brand hover:underline"
            >
              {isDescriptionExpanded ? 'Show less' : 'Show full description'}
            </button>
          </div>

          {job.source_url ? (
            <div>
              <dt className="text-xs font-medium text-gray-500">Source</dt>
              <dd className="mt-1">
                <a
                  href={job.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-brand hover:underline"
                >
                  View original posting
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                </a>
              </dd>
            </div>
          ) : null}
        </dl>
      )}
    </DetailPanel>
  )
}
