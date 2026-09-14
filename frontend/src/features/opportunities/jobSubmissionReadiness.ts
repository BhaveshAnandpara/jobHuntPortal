/**
 * The single definition of "can this account submit a job posting yet?",
 * shared by `JobUrlSubmitForm` (which blocks the input) and `DashboardPage`
 * (which swaps its whole primary-action slot for an onboarding CTA, per
 * T5 in docs/frontend/frontend-revamp-spec.md: "no resumes yet → should
 * point at /resumes, not just show zeros").
 *
 * Why it keys off `GET /profiles` and not `GET /resumes`: a job matched
 * against zero *active profiles* is auto-IGNOREd by the matching workflow
 * (workflows/langgraph/job_matching/nodes.py's NO_PROFILES_AVAILABLE
 * short-circuit), and a profile only exists once its resume reaches
 * `PARSED`. So "has an active profile" — not "has a resume row" — is the
 * condition that actually decides whether a submitted job can ever be
 * matched, and it is already the condition the submit form has always used.
 * Reusing that one query also means this adds **no** new API call to the
 * Dashboard: `useProfiles` is the same TanStack query key the form already
 * mounts, so the two callers share one in-flight request.
 *
 * Fails open while the fetch is loading or errored (`isSuccess` guard) —
 * a slow or briefly unavailable secondary API must never block the app's
 * primary action or flash an onboarding screen at an established user.
 *
 * Task 10 of docs/llm-provider-config/TECH.md adds a **second** condition
 * rather than replacing the first: `POST /jobs/ingest-url` is now hard-gated
 * server-side on a validated AI provider key too (Task 7), so both must hold
 * before submission is allowed. The LLM half of that lives in
 * `features/gating/llmConfigReadiness.ts` because resume upload is gated on
 * exactly the same condition and neither feature owns the other's action.
 *
 * Both blockers can be true at once on a brand-new account. `blocker` is a
 * single value, not two booleans for the UI to juggle, so callers can only
 * ever render one message: the resume one wins, because without an analyzed
 * resume there is nothing for a submitted job to be matched *against* — the
 * key alone wouldn't make submission useful, and a two-banner "you are
 * missing these two things" screen was explicitly not what any of the three
 * call sites already do for their single-blocker case.
 *
 * Owner: frontend-opportunities-agent.
 */

import { useProfiles } from '../../api/profiles'
import { useLlmConfigReadiness } from '../gating/llmConfigReadiness'

/** Which single gated state to render when submission is blocked. */
export type JobSubmissionBlocker = 'missing-resume' | 'missing-llm-config'

export type JobSubmissionReadiness = {
  /**
   * True only once `GET /profiles` has definitively resolved to zero
   * `ACTIVE` profiles — i.e. nothing a submitted job could be matched
   * against yet.
   */
  isBlockedOnMissingResume: boolean
  /**
   * True only once `GET /users/me/llm-config` has definitively resolved to
   * "no validated provider key" — see `llmConfigReadiness.ts`.
   */
  isBlockedOnMissingLlmConfig: boolean
  /**
   * The one blocker to show, resume first; `null` when submission is
   * allowed. `isBlockedOnMissingResume || isBlockedOnMissingLlmConfig` is
   * the same thing as `blocker !== null`.
   */
  blocker: JobSubmissionBlocker | null
}

export function useJobSubmissionReadiness(): JobSubmissionReadiness {
  const profiles = useProfiles()
  const { isBlockedOnMissingLlmConfig } = useLlmConfigReadiness()
  const hasActiveProfile = (profiles.data ?? []).some((profile) => profile.status === 'ACTIVE')
  const isBlockedOnMissingResume = profiles.isSuccess && !hasActiveProfile

  return {
    isBlockedOnMissingResume,
    isBlockedOnMissingLlmConfig,
    blocker: isBlockedOnMissingResume
      ? 'missing-resume'
      : isBlockedOnMissingLlmConfig
        ? 'missing-llm-config'
        : null,
  }
}
