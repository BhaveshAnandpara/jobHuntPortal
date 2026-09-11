/**
 * Presentational row for one resume on `/resumes` — extracted from
 * `ResumesPage.tsx` in T6 (docs/frontend/frontend-revamp-spec.md) so the
 * page file keeps only upload/replace/delete state and this file owns the
 * progressive-state styling.
 *
 * This component holds **no** state, no data fetching and no mutation
 * calls: it receives an already-fetched `ResumeResponse`, the matching
 * `ResumeProfile` (if one exists) and two callbacks. All polling still
 * lives in `useResumes` (2s, see api/resumes.ts) exactly as before.
 *
 * Progressive disclosure per `ResumeStatus`
 * (docs/frontend/async-workflows.md#resume-parsing-progressive-disclosure):
 *
 *   UPLOADED / PARSING  -> in-progress badge (StatusBadge's `progress`
 *                          category) plus an *indeterminate* progress bar.
 *                          Indeterminate on purpose: the backend reports no
 *                          percentage, and inventing one would be filler
 *                          (spec Section 3, item 1).
 *   PARSED              -> the derived profile summary. Gated on the resume's
 *                          own status, not merely on "a profile object
 *                          exists", so a stale `GET /profiles` entry can
 *                          never make a still-parsing resume look finished.
 *   PARSE_FAILED        -> a visually distinct (negative-accented) row that
 *                          stays in the list with its actions intact, so one
 *                          bad file never blocks the others.
 *
 * The summary stays profession-independent: title/seniority/experience/
 * summary/skills/education are rendered as free text exactly as
 * `ResumeProfile` returns them.
 */

import { AlertTriangle, FileText } from 'lucide-react'
import { Button, Card, StatusBadge } from '../../components'
import { formatDate } from '../../utils/format'
import type { ResumeProfile, ResumeResponse, ResumeStatus } from '../../api/types'

/** Non-terminal statuses — the same two `api/resumes.ts` keeps polling for. */
const IN_PROGRESS_STATUSES: ReadonlySet<ResumeStatus> = new Set(['UPLOADED', 'PARSING'])

function formatExperience(years: number | null | undefined): string | null {
  if (years === null || years === undefined) return null
  return `${years} ${years === 1 ? 'year' : 'years'} experience`
}

function formatEducation(entry: {
  institution: string
  degree?: string | null
  field_of_study?: string | null
  graduation_year?: number | null
}): string {
  const qualification = [entry.degree, entry.field_of_study].filter(Boolean).join(', ')
  const head = qualification ? `${qualification} — ${entry.institution}` : entry.institution
  return entry.graduation_year ? `${head} (${entry.graduation_year})` : head
}

function ProfileSummary({ profile }: { profile: ResumeProfile }) {
  const experience = formatExperience(profile.experience_years)
  const meta = [profile.seniority, experience].filter(Boolean) as string[]
  const education = profile.education ?? []

  return (
    <div className="mt-5 border-t border-gray-100 pt-5">
      <p className="text-xs font-medium tracking-wide text-gray-500 uppercase">Derived profile</p>
      <p className="mt-2 text-sm font-medium text-gray-900">{profile.title}</p>
      {meta.length > 0 ? <p className="mt-0.5 text-xs text-gray-500">{meta.join(' · ')}</p> : null}
      {profile.summary ? <p className="mt-2 text-sm text-gray-600">{profile.summary}</p> : null}

      {profile.skills.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Skills">
          {profile.skills.map((skill) => (
            <li
              key={skill}
              className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-700 ring-1 ring-gray-200 ring-inset"
            >
              {skill}
            </li>
          ))}
        </ul>
      ) : null}

      {education.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-0.5" aria-label="Education">
          {education.map((entry) => (
            <li key={`${entry.institution}-${entry.degree ?? ''}-${entry.graduation_year ?? ''}`} className="text-xs text-gray-500">
              {formatEducation(entry)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

type ResumeCardProps = {
  resume: ResumeResponse
  /** The derived profile for this resume, if `GET /profiles` has one yet. */
  profile: ResumeProfile | undefined
  /** True while any replace is mid-flight — disables every row's Replace. */
  isReplaceDisabled: boolean
  onReplace: () => void
  onDelete: (trigger: HTMLButtonElement) => void
}

export function ResumeCard({ resume, profile, isReplaceDisabled, onReplace, onDelete }: ResumeCardProps) {
  const isInProgress = IN_PROGRESS_STATUSES.has(resume.status)
  const hasFailed = resume.status === 'PARSE_FAILED'
  // Gated on the resume's own terminal status, never on profile presence
  // alone — see the file header.
  const showProfile = resume.status === 'PARSED' && profile !== undefined

  return (
    <Card
      className={
        hasFailed
          ? 'border-l-4 border-l-status-negative transition-shadow hover:shadow-md'
          : 'transition-shadow hover:shadow-md'
      }
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <FileText className="mt-0.5 h-5 w-5 shrink-0 text-gray-400" aria-hidden />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-gray-900">{resume.file_name}</p>
            <p className="text-xs text-gray-500">Uploaded {formatDate(resume.uploaded_at)}</p>
          </div>
        </div>
        <StatusBadge status={resume.status} />
      </div>

      {isInProgress ? (
        <div className="mt-4">
          {/*
            Indeterminate by design — `role="progressbar"` with no
            `aria-valuenow` is the ARIA-sanctioned way to say "in progress,
            duration unknown", which is exactly what the backend reports.
          */}
          <div
            role="progressbar"
            aria-label={`Analyzing ${resume.file_name}`}
            className="h-1 w-full overflow-hidden rounded-full bg-status-progress-bg"
          >
            <div className="h-full w-full animate-pulse rounded-full bg-status-progress/60" />
          </div>
          <p className="mt-2 text-xs text-gray-500">
            Analyzing this resume. The derived profile appears here automatically — you can leave this page open.
          </p>
        </div>
      ) : null}

      {showProfile ? <ProfileSummary profile={profile} /> : null}

      {hasFailed ? (
        <div className="mt-5 flex items-start gap-2 rounded-md bg-status-negative-bg px-3 py-2.5">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-negative" aria-hidden />
          <p className="text-sm text-status-negative">
            This resume could not be analyzed, so it has no profile. Your other resumes are unaffected — replace this
            file with a corrected one to try again.
          </p>
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={isReplaceDisabled} onClick={onReplace}>
          Replace
        </Button>
        <Button variant="destructive" onClick={(event) => onDelete(event.currentTarget)}>
          Delete
        </Button>
      </div>
    </Card>
  )
}
