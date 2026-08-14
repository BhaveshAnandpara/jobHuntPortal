/**
 * Manual, user-triggered `ApplicationStatus` transitions available from the
 * Status Action Menu on Opportunity Detail — pre-filters the menu's options
 * to what docs/architecture/state-machines.md#opportunity-lifecycle
 * documents as valid manual transitions via
 * `PATCH /applications/{id}/status`.
 *
 * This is a UX nicety, NOT the real authority — see
 * docs/frontend/error-handling.md#invalid-state-transition-ux-a-specific-case-of-400-validation_error.
 * The backend still validates every transition and can reject a selected
 * option (e.g. a stale menu after a concurrent update elsewhere), in which
 * case the caller must show the error and refetch current state rather than
 * trusting this table.
 *
 * Derived directly from state-machines.md's linear chain plus its
 * "Additional edges" table:
 * - Every state from SHORTLISTED through REFERRED (plus DISCOVERED/MATCHED)
 *   may jump straight to APPLIED ("a user may apply directly without
 *   receiving a referral").
 * - MATCHED may also go to IGNORED manually ("manual dismiss (BORDERLINE)").
 * - OUTREACH_SENT may advance to REFERRED ("user confirms a referral
 *   occurred").
 * - APPLIED may advance to INTERVIEW, or REJECTED directly (no interview).
 * - INTERVIEW may resolve to OFFER or REJECTED.
 * - Any non-terminal status may move to WITHDRAWN.
 * - OFFER/REJECTED/IGNORED/WITHDRAWN are terminal — no further transitions.
 */

import type { ApplicationStatus } from '../../api/types'

const MANUAL_TRANSITIONS: Record<ApplicationStatus, ApplicationStatus[]> = {
  DISCOVERED: ['APPLIED', 'WITHDRAWN'],
  MATCHED: ['APPLIED', 'IGNORED', 'WITHDRAWN'],
  SHORTLISTED: ['APPLIED', 'WITHDRAWN'],
  CONTACT_SEARCH: ['APPLIED', 'WITHDRAWN'],
  CONTACT_FOUND: ['APPLIED', 'WITHDRAWN'],
  OUTREACH_GENERATED: ['APPLIED', 'WITHDRAWN'],
  OUTREACH_APPROVED: ['APPLIED', 'WITHDRAWN'],
  OUTREACH_SENT: ['REFERRED', 'APPLIED', 'WITHDRAWN'],
  REFERRED: ['APPLIED', 'WITHDRAWN'],
  APPLIED: ['INTERVIEW', 'REJECTED', 'WITHDRAWN'],
  INTERVIEW: ['OFFER', 'REJECTED', 'WITHDRAWN'],
  OFFER: [],
  REJECTED: [],
  IGNORED: [],
  WITHDRAWN: [],
}

export function manualTransitionsFrom(status: ApplicationStatus): ApplicationStatus[] {
  return MANUAL_TRANSITIONS[status] ?? []
}
