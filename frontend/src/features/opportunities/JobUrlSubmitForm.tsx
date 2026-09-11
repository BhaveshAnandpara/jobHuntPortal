/**
 * The primary action across the app: paste a job posting URL. Shared between
 * the Dashboard and (lightly duplicated on) the Opportunities page — see
 * docs/frontend/routes.md#--dashboard and
 * docs/frontend/user-flows.md#job-submission-flow.
 *
 * Handling per user-flows.md:
 * - Client-side URL format validation (cheap UX check only — the backend's
 *   INVALID_JOB_URL is still the real authority).
 * - On success: no `Application` exists yet (Tracking Service creates it
 *   asynchronously off `jobs.discovered`), so we navigate to `/opportunities`
 *   (the list), never a detail page — passing the ingested job's
 *   company/title/id as router state so the list can optimistically
 *   indicate "submitted, analyzing…" until polling surfaces the real row.
 * - Duplicate-job behavior needs no special handling — the backend already
 *   dedupes idempotently, so a re-submission of the same URL is treated
 *   uniformly with any other successful response.
 * - Ingestion failure: `ApiError` message shown inline, form stays
 *   populated for correction.
 *
 * T5 (docs/frontend/frontend-revamp-spec.md) touched this file for the
 * Dashboard restyle. **It is shared with `/opportunities` (T7), so the
 * changes were held to presentation plus one added hint line:**
 * - `INVALID_JOB_URL` (400) now gets a specific inline hint under the input
 *   alongside the backend's own message — T5's "inline error under the URL
 *   input for INVALID_JOB_URL, not a generic toast" item. Every other
 *   ingestion failure keeps the existing inline treatment described above
 *   (what error-handling.md's row prescribes and what
 *   `tests/e2e/error-recovery.spec.ts` asserts); no toast was introduced.
 * - The "no active resume yet" predicate moved verbatim into
 *   `jobSubmissionReadiness.ts` so this form and the Dashboard's onboarding
 *   empty state can't drift apart. Behavior and copy are unchanged.
 *
 * Owner: frontend-opportunities-agent.
 */

import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { Link, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { Button, FieldError, Input } from '../../components'
import { useIngestJobUrl } from '../../api/jobs'
import { toApiError } from '../../api/client'
import { httpUrl } from '../../utils/validation'
import { useJobSubmissionReadiness } from './jobSubmissionReadiness'

const schema = z.object({ url: httpUrl })
type FormValues = z.infer<typeof schema>

export function JobUrlSubmitForm({ className = '' }: { className?: string }) {
  const navigate = useNavigate()
  const ingest = useIngestJobUrl()
  const { isBlockedOnMissingResume } = useJobSubmissionReadiness()
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = handleSubmit((values) => {
    ingest.mutate(
      { url: values.url },
      {
        onSuccess: (job) => {
          navigate('/opportunities', {
            state: { submittedJob: { jobId: job.id, company: job.company, title: job.title } },
          })
        },
      },
    )
  })

  const submitError = ingest.isError ? toApiError(ingest.error) : undefined

  // Gate submission on having at least one active resume: a job matched
  // against zero active profiles is auto-IGNOREd by the matching workflow
  // (see workflows/langgraph/job_matching/nodes.py's NO_PROFILES_AVAILABLE
  // short-circuit), and re-matching an already-discovered job once a resume
  // is added later isn't implemented yet (matching/consumers.py's
  // handle_profile_updated is a deferred stub) — so a job submitted before
  // any active profile exists can never be matched. Fails open while the
  // profiles fetch is loading/erroring (shows the form as usual) rather
  // than blocking the primary action — and flashing a skeleton — on a
  // secondary API being slow or briefly unavailable; only flips to the
  // blocked message once the fetch has definitively resolved to zero
  // active profiles. See `jobSubmissionReadiness.ts`.
  if (isBlockedOnMissingResume) {
    return (
      <div className={className}>
        <p className="mb-1 text-sm font-medium text-gray-700">Paste a job posting URL</p>
        <p className="text-sm text-gray-500">
          Upload a resume before adding opportunities — matching needs at least one active resume to
          compare jobs against.{' '}
          <Link to="/resumes" className="font-medium text-brand hover:underline">
            Upload a resume
          </Link>
        </p>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className={className} noValidate>
      <label htmlFor="job-url" className="block text-sm font-medium text-gray-900">
        Paste a job posting URL
      </label>
      <p className="mt-0.5 mb-2 text-xs text-gray-500">
        The posting is fetched and matched against your resume automatically — it appears in
        Opportunities once that finishes.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="flex-1">
          <Input
            id="job-url"
            type="url"
            placeholder="https://company.com/careers/job/123"
            invalid={Boolean(errors.url) || ingest.isError}
            {...register('url')}
          />
          <FieldError message={errors.url?.message ?? submitError?.message} />
          {/*
            The one error code with a real, actionable next step for the user
            — see error-handling.md's INVALID_JOB_URL row. The backend's own
            message stays above it verbatim; this only adds what to try.
          */}
          {submitError?.code === 'INVALID_JOB_URL' ? (
            <p className="mt-1 text-xs text-gray-500">
              Check the link opens the posting itself — a search-results page, or one that needs a
              login, can&apos;t be read.
            </p>
          ) : null}
        </div>
        <Button type="submit" variant="primary" isLoading={ingest.isPending} className="sm:shrink-0">
          Submit
        </Button>
      </div>
    </form>
  )
}
