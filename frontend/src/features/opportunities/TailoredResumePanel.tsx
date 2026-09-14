/**
 * Tailored resume panel — Opportunity Detail (docs/tailored-resume/TECH.md
 * Task 7). Offers to generate a job-specific `.docx` version of the resume
 * that won this job's match, and — when one exists — hands it to the user.
 *
 * **Why it gates on the match stage first.** Tailoring is defined entirely
 * relative to this job's existing `match_score`: the backend only persists a
 * document when the tailored version scores at least 5 points higher than
 * that baseline. With no `JobMatch` there is no baseline to beat, and
 * `POST` would answer `NOT_FOUND`. So this panel asks the same question
 * `MatchPanel` already asks (`lifecycleStage.ts`, via the page's
 * `shouldRenderStageData`) and, before matching has run, says the step isn't
 * available yet instead of offering a button that can only fail.
 *
 * **The state machine**, in the order it is evaluated — the ordering is the
 * design, not an implementation detail:
 *
 * 1. matching hasn't run  -> `StageNotReached`
 * 2. the "does one already exist?" query is still in flight -> `PanelLoading`
 * 3. a document exists (from this session's generate, or a previous one)
 *    -> the score comparison + Download. **This wins over the LLM-config
 *    gate**, matching the backend: only `POST` is gated, because reading and
 *    downloading spend nobody's quota, so a user whose key was removed can
 *    still retrieve what they already generated.
 * 4. a generate call is in flight -> the slow-work notice (see below)
 * 5. the last generate answered "no improvement" -> that message, and
 *    deliberately **no** button to try again: nothing about the job or the
 *    profile has changed, so a second identical run would spend the user's
 *    LLM quota to reach the same answer (PRODUCT.md's honest-gating goal)
 * 6. a generate call failed -> the server's own message, retryable; except
 *    an `LLM_NOT_CONFIGURED` rejection, which is the gate rather than a
 *    fault and renders as the gated state
 * 7. no validated AI provider key -> the shared gated empty state
 * 8. otherwise -> idle: what this does, the 5-point bar, and the button
 *
 * **Why the loading state has words.** This is the slowest call in the app:
 * two LLM round-trips (generate, then re-score with the same scorer normal
 * matching uses) plus document generation, and possibly a one-off re-parse
 * of an older resume before any of that. A bare spinner for that long reads
 * as a hung page, so the wait says what is happening and roughly how long.
 *
 * Reuses `features/gating/LlmConfigRequired` rather than re-explaining the
 * missing key — that component exists precisely so the explanation can't
 * drift between the actions it blocks.
 *
 * Owner: frontend-opportunities-agent.
 */

import { Download, FileText, Sparkles } from 'lucide-react'
import { Button, ErrorState } from '../../components'
import { DetailPanel, PanelLoading, StageNotReached } from './DetailPanel'
import { LlmConfigRequiredEmptyState } from '../gating/LlmConfigRequired'
import { useLlmConfigReadiness } from '../gating/llmConfigReadiness'
import {
  useDownloadTailoredResume,
  useGenerateTailoredResume,
  useTailoredResume,
} from '../../api/tailoredResume'
import { toApiError } from '../../api/client'
import type { TailoringResponse } from '../../api/types'
import { formatScorePercent } from '../../utils/format'

type TailoredResumePanelProps = {
  jobId: string
  /** False only when the matching stage provably hasn't run yet — see lifecycleStage.ts. */
  isAvailable: boolean
}

const PANEL_TITLE = 'Tailored resume'

const PANEL_DESCRIPTION = 'A job-specific version of your resume, generated on request.'

/** The gate's own error code, returned by `POST` when the user has no
 * validated provider key — see `src/resume_tailoring/api.py`. */
const LLM_NOT_CONFIGURED_CODE = 'LLM_NOT_CONFIGURED'

const GATED_DESCRIPTION =
  'Add your AI provider key in Settings to generate a tailored resume for this job.'

function ScoreComparison({ baseScore, achievedScore }: { baseScore: number; achievedScore: number }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-sm text-gray-500">Match score</span>
      <span className="text-sm tabular-nums text-gray-500 line-through">
        {formatScorePercent(baseScore)}
      </span>
      <span aria-hidden className="text-sm text-gray-400">
        →
      </span>
      <span className="text-2xl font-semibold tabular-nums text-status-positive">
        {formatScorePercent(achievedScore)}
      </span>
    </div>
  )
}

/**
 * Skill chips in the exact shape `MatchPanel`'s own `SkillList` renders them
 * — same pill geometry, same `status-positive`/`status-negative` token pair —
 * so "strengths and gaps" reads identically whether the user is looking at the
 * original match or at what tailoring changed. No empty branch: every caller
 * omits the whole section rather than showing a hollow heading.
 */
function SkillTagList({
  label,
  skills,
  tone,
}: {
  label: string
  skills: string[]
  tone: 'positive' | 'negative'
}) {
  const toneClasses =
    tone === 'positive'
      ? 'bg-status-positive-bg text-status-positive'
      : 'bg-status-negative-bg text-status-negative'

  return (
    <div>
      <p className="text-xs font-medium text-gray-500">{label}</p>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {skills.map((skill) => (
          <li
            key={skill}
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${toneClasses}`}
          >
            {skill}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * The "why did the score move" breakdown, rendered on both generate outcomes
 * (improved and not-improved) — a user told their resume is already
 * well-suited is exactly the user who wants to see what is already strong and
 * what is still missing.
 *
 * **"Skills tailoring emphasized" is a set difference, not a list the backend
 * sends.** `tailored_matched_skills` minus `base_matched_skills` isolates the
 * skills that only started matching *because* of the rewrite; a skill matched
 * before and after was already being credited, so claiming tailoring
 * emphasized it would overstate what the run actually did.
 *
 * Each section disappears when its list is empty rather than rendering an
 * empty heading — "Still missing" over nothing would read as a broken panel on
 * the genuinely fully-matched profile it is describing.
 *
 * The `?? []` guards are load-bearing despite the generated types marking
 * these four as required: they are defaulted (`= []`) Pydantic fields, so a
 * body predating them — or any response that simply omits them — arrives here
 * with `undefined`, and a bare `new Set(undefined)` would take the panel down.
 */
function SkillBreakdown({ result, className }: { result: TailoringResponse; className?: string }) {
  const baseMatched = new Set(result.base_matched_skills ?? [])
  const emphasized = (result.tailored_matched_skills ?? []).filter(
    (skill) => !baseMatched.has(skill),
  )
  const stillMissing = result.tailored_missing_skills ?? []

  // Returning null (rather than an empty wrapper) is what lets the caller
  // attach spacing via `className` without leaving a floating margin behind
  // when there is nothing to space.
  if (emphasized.length === 0 && stillMissing.length === 0) {
    return null
  }

  return (
    <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 ${className ?? ''}`}>
      {emphasized.length > 0 ? (
        <SkillTagList label="Skills tailoring emphasized" skills={emphasized} tone="positive" />
      ) : null}
      {stillMissing.length > 0 ? (
        <SkillTagList label="Still missing" skills={stillMissing} tone="negative" />
      ) : null}
    </div>
  )
}

export function TailoredResumePanel({ jobId, isAvailable }: TailoredResumePanelProps) {
  const { isBlockedOnMissingLlmConfig } = useLlmConfigReadiness()

  // Disabled until matching has run: with no baseline score there is nothing
  // for a tailored version to have been compared against, so the request
  // could only ever 404.
  const existingQuery = useTailoredResume(jobId, { enabled: isAvailable })
  const generate = useGenerateTailoredResume(jobId)
  const download = useDownloadTailoredResume(jobId)

  const existingError = existingQuery.isError ? toApiError(existingQuery.error) : undefined
  // A 404 is this route's "none generated yet" answer, not a failure.
  const existingFailed = existingError != null && existingError.status !== 404

  const generateError = generate.isError ? toApiError(generate.error) : undefined
  const isGenerateBlockedOnLlmConfig = generateError?.code === LLM_NOT_CONFIGURED_CODE

  // The freshly generated result wins over the stored one: after a
  // successful run the mutation's own response is the newest truth, and it
  // is already on hand before the invalidated query has refetched.
  const generated = generate.data?.improved ? generate.data : undefined
  // Named `tailoredDocument`, not `document` — shadowing the global would
  // silently break any later DOM access in this component.
  const tailoredDocument = generated ?? existingQuery.data

  if (!isAvailable) {
    return (
      <DetailPanel title={PANEL_TITLE}>
        <StageNotReached
          icon={<Sparkles className="h-5 w-5" aria-hidden />}
          title="Nothing to tailor against yet"
          description="Tailoring is measured against this job's match score, so it becomes available once this posting has been matched to your resumes."
        />
      </DetailPanel>
    )
  }

  if (existingQuery.isLoading) {
    return (
      <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
        <PanelLoading label="Loading tailored resume" lines={2} />
      </DetailPanel>
    )
  }

  if (tailoredDocument) {
    return (
      <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
        <div className="flex flex-col gap-4">
          <div>
            <ScoreComparison
              baseScore={tailoredDocument.base_score}
              achievedScore={tailoredDocument.achieved_score}
            />
            <p className="mt-1.5 text-xs text-gray-500">
              Scored by the same analysis used for this job's original match. Review and edit the
              document before you send it anywhere.
            </p>
          </div>

          {/* Only the freshly generated result carries the skill lists: the
              stored record `GET` returns is scores and a timestamp, so a
              document recovered from a previous session shows the comparison
              without a breakdown rather than a fabricated one. */}
          {generated ? <SkillBreakdown result={generated} /> : null}

          <div>
            <Button
              variant="primary"
              onClick={() => download.mutate()}
              isLoading={download.isPending}
            >
              <Download className="h-4 w-4" aria-hidden />
              Download tailored resume (.docx)
            </Button>
            {download.isError ? (
              <p role="alert" className="mt-2 text-xs text-status-negative">
                {toApiError(download.error).message}
              </p>
            ) : null}
          </div>
        </div>
      </DetailPanel>
    )
  }

  if (generate.isPending) {
    return (
      <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
        <div role="status" className="flex items-start gap-3 rounded-md bg-gray-50 px-4 py-4">
          <FileText className="mt-0.5 h-5 w-5 shrink-0 animate-pulse text-gray-400" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-700">
              Generating and scoring a tailored version — this can take up to a minute.
            </p>
            <p className="mt-0.5 text-xs text-gray-500">
              Your resume is being rewritten for this job and then re-scored against it, so leaving
              this page won't lose the result.
            </p>
          </div>
        </div>
      </DetailPanel>
    )
  }

  if (generate.isSuccess && !generate.data.improved) {
    return (
      <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
        <div className="rounded-md border border-gray-200 bg-gray-50 px-4 py-4">
          <p className="text-sm font-medium text-gray-700">
            Your current resume is already well-suited for this job.
          </p>
          <p className="mt-1 text-xs text-gray-500">
            A tailored version scored {formatScorePercent(generate.data.achieved_score)} against
            this job's {formatScorePercent(generate.data.base_score)} — not enough of a gain to be
            worth a separate document. Nothing was created.
          </p>
          {/* Shown here too: no document was produced, but the run still
              measured what already matches and what does not, and that is the
              most actionable thing this outcome has to offer. */}
          <SkillBreakdown result={generate.data} className="mt-4" />
        </div>
      </DetailPanel>
    )
  }

  if (generateError && !isGenerateBlockedOnLlmConfig) {
    return (
      <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
        <ErrorState message={generateError.message} onRetry={() => generate.mutate()} />
      </DetailPanel>
    )
  }

  if (isBlockedOnMissingLlmConfig || isGenerateBlockedOnLlmConfig) {
    return (
      <DetailPanel title={PANEL_TITLE}>
        <LlmConfigRequiredEmptyState description={GATED_DESCRIPTION} />
      </DetailPanel>
    )
  }

  return (
    <DetailPanel title={PANEL_TITLE} description={PANEL_DESCRIPTION}>
      <div className="flex flex-col gap-4">
        <div>
          <p className="text-sm text-gray-700">
            Rewrite the resume that won this match so it leads with what this posting actually asks
            for, then download it as an editable <code className="text-xs">.docx</code>.
          </p>
          <p className="mt-1.5 text-xs text-gray-500">
            We'll only create a document if it would meaningfully improve your match score — at
            least 5 points, measured by the same analysis that scored this job in the first place.
            Nothing you haven't already told us is invented.
          </p>
          {existingFailed ? (
            <p role="alert" className="mt-2 text-xs text-status-negative">
              {existingError.message}
            </p>
          ) : null}
        </div>

        <div>
          <Button variant="primary" onClick={() => generate.mutate()}>
            Generate tailored resume
          </Button>
        </div>
      </div>
    </DetailPanel>
  )
}
