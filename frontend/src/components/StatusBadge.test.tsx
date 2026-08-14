/**
 * Covers every enum value listed in docs/frontend/design-system.md's status
 * badge table, plus every remaining value of each backend enum this app
 * renders (per agent-ownership.md's "none falls through to an unstyled
 * default" requirement) — see src/api/generated/schema.d.ts for the literal
 * union values these arrays mirror.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusBadge } from './StatusBadge'
import type {
  ApplicationStatus,
  ContactStatus,
  JobProcessingStatus,
  MatchRecommendation,
  OutreachStatus,
  ResumeStatus,
} from '../api/types'

const APPLICATION_STATUSES: ApplicationStatus[] = [
  'DISCOVERED',
  'MATCHED',
  'SHORTLISTED',
  'CONTACT_SEARCH',
  'CONTACT_FOUND',
  'OUTREACH_GENERATED',
  'OUTREACH_APPROVED',
  'OUTREACH_SENT',
  'REFERRED',
  'APPLIED',
  'INTERVIEW',
  'OFFER',
  'REJECTED',
  'IGNORED',
  'WITHDRAWN',
]

const OUTREACH_STATUSES: OutreachStatus[] = [
  'DRAFT',
  'PENDING_APPROVAL',
  'APPROVED',
  'REJECTED',
  'EDITED',
  'SENT',
  'SEND_FAILED',
]

const RESUME_STATUSES: ResumeStatus[] = ['UPLOADED', 'PARSING', 'PARSED', 'PARSE_FAILED', 'ARCHIVED']

const CONTACT_STATUSES: ContactStatus[] = ['DISCOVERED', 'RANKED', 'ARCHIVED']

const JOB_PROCESSING_STATUSES: JobProcessingStatus[] = ['PENDING', 'NORMALIZED', 'MATCHED', 'FAILED']

const MATCH_RECOMMENDATIONS: MatchRecommendation[] = ['SHORTLIST', 'BORDERLINE', 'IGNORE']

const ALL_STATUSES = Array.from(
  new Set([
    ...APPLICATION_STATUSES,
    ...OUTREACH_STATUSES,
    ...RESUME_STATUSES,
    ...CONTACT_STATUSES,
    ...JOB_PROCESSING_STATUSES,
    ...MATCH_RECOMMENDATIONS,
  ]),
)

const CATEGORY_BG_CLASS = [
  'bg-status-progress-bg',
  'bg-status-attention-bg',
  'bg-status-positive-bg',
  'bg-status-negative-bg',
  'bg-status-neutral-bg',
]

describe('StatusBadge', () => {
  it.each(ALL_STATUSES)('renders a styled badge for %s with no missing case', (status) => {
    const { container } = render(<StatusBadge status={status} />)
    const badge = container.querySelector('span')
    expect(badge).not.toBeNull()
    expect(badge?.textContent).not.toBe('')
    // Every rendered badge must match exactly one of the five category
    // background classes — never fall through unstyled.
    const matchedCategories = CATEGORY_BG_CLASS.filter((cls) => badge?.className.includes(cls))
    expect(matchedCategories).toHaveLength(1)
  })

  // The design-system.md#status-badges table pins these exact
  // category/color assignments — this test locks in the two mismatches
  // found and fixed in src/utils/status.ts against the first-draft skeleton.
  it('categorizes OUTREACH_GENERATED as positive (pipeline-progress milestone), per design-system.md', () => {
    render(<StatusBadge status="OUTREACH_GENERATED" />)
    expect(screen.getByText(/outreach ready for review/i).className).toContain('bg-status-positive-bg')
  })

  it('categorizes IGNORED as negative/stopped, per design-system.md', () => {
    render(<StatusBadge status="IGNORED" />)
    expect(screen.getByText('Ignored').className).toContain('bg-status-negative-bg')
  })

  it('shows a subtle pulsing dot only for the "progress" category, never a spinner', () => {
    const { container: progressContainer } = render(<StatusBadge status="PARSING" />)
    expect(progressContainer.querySelector('.animate-pulse')).toBeInTheDocument()

    const { container: positiveContainer } = render(<StatusBadge status="PARSED" />)
    expect(positiveContainer.querySelector('.animate-pulse')).not.toBeInTheDocument()
  })
})
