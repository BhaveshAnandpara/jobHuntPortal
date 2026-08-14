/**
 * Central enum -> presentation mapping. See
 * docs/frontend/design-system.md#status-badges. Every lifecycle enum this
 * app renders (`ApplicationStatus`, `OutreachStatus`, `ResumeStatus`,
 * `ContactStatus`, `JobProcessingStatus`, `MatchRecommendation`) maps to
 * one of five UI *categories* here — never a new value, never a new
 * lifecycle state invented on the frontend. The backend enum (imported
 * from src/api/types.ts, generated from the OpenAPI schema) remains the
 * only source of truth for which values exist.
 *
 * Owner: frontend-design-agent (this file lives alongside the
 * `StatusBadge` component that consumes it). Feature agents call
 * `getStatusPresentation(...)`; they do not add their own label/color
 * logic per feature.
 */

import type {
  ApplicationStatus,
  ContactStatus,
  JobProcessingStatus,
  MatchRecommendation,
  OutreachStatus,
  ResumeStatus,
} from '../api/types'

export type StatusCategory = 'progress' | 'attention' | 'positive' | 'negative' | 'neutral'

export type StatusPresentation = {
  label: string
  category: StatusCategory
}

type AnyKnownStatus =
  | ApplicationStatus
  | OutreachStatus
  | ResumeStatus
  | ContactStatus
  | JobProcessingStatus
  | MatchRecommendation

/**
 * One flat table across every enum this app knows about. Keys are the
 * exact backend enum values (see shared/types/enums.py) — if the backend
 * adds a new value, this table gets one new row, nothing else changes.
 */
const STATUS_PRESENTATION: Record<AnyKnownStatus, StatusPresentation> = {
  // ApplicationStatus
  DISCOVERED: { label: 'Analyzing…', category: 'progress' },
  MATCHED: { label: 'Matched', category: 'progress' },
  SHORTLISTED: { label: 'Shortlisted', category: 'positive' },
  CONTACT_SEARCH: { label: 'Finding contacts…', category: 'progress' },
  CONTACT_FOUND: { label: 'Contacts found', category: 'positive' },
  // Per design-system.md#status-badges, OUTREACH_GENERATED is categorized
  // "Positive / advanced" (a pipeline-progress milestone), not "needs your
  // attention" — that category is reserved for OutreachStatus.PENDING_APPROVAL
  // / EDITED, the values shown once the user is actually on the review screen.
  OUTREACH_GENERATED: { label: 'Outreach ready for review', category: 'positive' },
  OUTREACH_APPROVED: { label: 'Outreach approved', category: 'positive' },
  OUTREACH_SENT: { label: 'Outreach sent', category: 'positive' },
  REFERRED: { label: 'Referred', category: 'positive' },
  APPLIED: { label: 'Applied', category: 'positive' },
  INTERVIEW: { label: 'Interview', category: 'positive' },
  OFFER: { label: 'Offer', category: 'positive' },
  REJECTED: { label: 'Rejected', category: 'negative' },
  // Per design-system.md#status-badges, IGNORED is categorized
  // "Negative / stopped", not neutral — an ignored opportunity stopped
  // moving forward, distinct from an archived/withdrawn one.
  IGNORED: { label: 'Ignored', category: 'negative' },
  WITHDRAWN: { label: 'Withdrawn', category: 'neutral' },

  // OutreachStatus (DRAFT/APPLIED/REJECTED overlap in spelling only, not
  // value, with ApplicationStatus above — TypeScript keys them once since
  // the string values are shared where they happen to coincide)
  DRAFT: { label: 'Draft', category: 'progress' },
  PENDING_APPROVAL: { label: 'Needs your review', category: 'attention' },
  APPROVED: { label: 'Approved', category: 'positive' },
  EDITED: { label: 'Edited, needs your review', category: 'attention' },
  SENT: { label: 'Sent', category: 'positive' },
  SEND_FAILED: { label: 'Send failed', category: 'negative' },

  // ResumeStatus
  UPLOADED: { label: 'Uploaded', category: 'progress' },
  PARSING: { label: 'Parsing…', category: 'progress' },
  PARSED: { label: 'Parsed', category: 'positive' },
  PARSE_FAILED: { label: 'Parse failed', category: 'negative' },
  ARCHIVED: { label: 'Archived', category: 'neutral' },

  // ContactStatus
  RANKED: { label: 'Ranked', category: 'positive' },

  // JobProcessingStatus
  PENDING: { label: 'Pending', category: 'progress' },
  NORMALIZED: { label: 'Normalized', category: 'progress' },
  FAILED: { label: 'Failed', category: 'negative' },

  // MatchRecommendation
  SHORTLIST: { label: 'Shortlist', category: 'positive' },
  BORDERLINE: { label: 'Borderline', category: 'attention' },
  IGNORE: { label: 'Not a match', category: 'neutral' },
}

export function getStatusPresentation(status: AnyKnownStatus): StatusPresentation {
  return STATUS_PRESENTATION[status]
}
