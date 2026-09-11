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
 * Owner: frontend-opportunities-agent.
 */

import { useProfiles } from '../../api/profiles'

export type JobSubmissionReadiness = {
  /**
   * True only once `GET /profiles` has definitively resolved to zero
   * `ACTIVE` profiles — i.e. nothing a submitted job could be matched
   * against yet.
   */
  isBlockedOnMissingResume: boolean
}

export function useJobSubmissionReadiness(): JobSubmissionReadiness {
  const profiles = useProfiles()
  const hasActiveProfile = (profiles.data ?? []).some((profile) => profile.status === 'ACTIVE')

  return { isBlockedOnMissingResume: profiles.isSuccess && !hasActiveProfile }
}
