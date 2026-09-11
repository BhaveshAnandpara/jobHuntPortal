/**
 * Unit tests for T8's progressive-panel-unlocking rule
 * (docs/frontend/frontend-revamp-spec.md, T8).
 *
 * The behavior under test is a three-way answer, not a boolean: a stage is
 * `reached` (render data), `not-reached` (render the "hasn't started"
 * placeholder), or `unknown` (can't tell — render data rather than assert a
 * negative). Most of these cases exist because `ApplicationStatus` alone
 * cannot answer the question: a user can jump straight to `APPLIED` or
 * `WITHDRAWN` from almost anywhere, which erases any hint of how far the
 * automated chain got.
 */

import { describe, expect, it } from 'vitest'
import {
  chainRank,
  furthestChainRank,
  getStageAvailability,
  shouldRenderStageData,
} from './lifecycleStage'
import type {
  ApplicationHistoryResponse,
  ApplicationResponse,
  ApplicationStatus,
} from '../../api/types'

function application(overrides: Partial<ApplicationResponse> = {}): ApplicationResponse {
  return {
    id: 'app-1',
    job_id: 'job-1',
    user_id: 'user-1',
    company: 'Acme Robotics',
    title: 'Senior Backend Engineer',
    status: 'DISCOVERED',
    selected_resume_id: null,
    match_score: null,
    matched_skills: [],
    missing_skills: [],
    discovered_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as ApplicationResponse
}

function historyEntry(
  toStatus: ApplicationStatus,
  fromStatus: ApplicationStatus | null = null,
): ApplicationHistoryResponse {
  return {
    id: `hist-${toStatus}`,
    application_id: 'app-1',
    from_status: fromStatus,
    to_status: toStatus,
    changed_at: '2026-01-01T00:00:00Z',
    triggered_by: 'system',
    source_event_type: null,
    correlation_id: null,
  } as ApplicationHistoryResponse
}

describe('chainRank', () => {
  it('ranks the automated chain in lifecycle order', () => {
    expect(chainRank('DISCOVERED')).toBe(0)
    expect(chainRank('MATCHED')).toBe(1)
    expect(chainRank('CONTACT_SEARCH')).toBe(3)
    expect(chainRank('OUTREACH_GENERATED')).toBe(5)
    expect(chainRank('REFERRED')).toBe(8)
  })

  it('refuses to rank manual/terminal statuses, which say nothing about chain progress', () => {
    // These are reachable manually from almost any state (statusTransitions.ts),
    // so force-fitting them onto the chain would fabricate progress.
    for (const status of ['APPLIED', 'INTERVIEW', 'OFFER', 'REJECTED', 'IGNORED', 'WITHDRAWN'] as const) {
      expect(chainRank(status)).toBeNull()
    }
    expect(chainRank(null)).toBeNull()
    expect(chainRank(undefined)).toBeNull()
  })
})

describe('furthestChainRank', () => {
  it('is null when nothing available can answer', () => {
    expect(furthestChainRank(application({ status: 'WITHDRAWN' }), undefined)).toBeNull()
    expect(furthestChainRank(undefined, undefined)).toBeNull()
  })

  it('reads an on-chain current status directly', () => {
    expect(furthestChainRank(application({ status: 'CONTACT_FOUND' }), undefined)).toBe(4)
  })

  it('treats a non-null match_score as proof matching ran', () => {
    // Status says nothing (manual jump to APPLIED), but a score only exists
    // because the matching workflow produced one.
    expect(furthestChainRank(application({ status: 'APPLIED', match_score: 0.9 }), undefined)).toBe(1)
  })

  it('recovers progress from history when the current status has erased it', () => {
    const history = [historyEntry('DISCOVERED'), historyEntry('MATCHED'), historyEntry('CONTACT_FOUND')]
    expect(furthestChainRank(application({ status: 'WITHDRAWN' }), history)).toBe(4)
  })

  it('counts from_status too, so a stage exited but never entered still counts', () => {
    // No row has OUTREACH_GENERATED as a destination, but one records leaving it.
    const history = [historyEntry('WITHDRAWN', 'OUTREACH_GENERATED')]
    expect(furthestChainRank(application({ status: 'WITHDRAWN' }), history)).toBe(5)
  })

  it('takes the maximum, so an earlier current status never cancels later evidence', () => {
    const history = [historyEntry('OUTREACH_SENT')]
    expect(furthestChainRank(application({ status: 'DISCOVERED' }), history)).toBe(7)
  })
})

describe('getStageAvailability', () => {
  it('locks every stage for a freshly discovered opportunity', () => {
    const app = application({ status: 'DISCOVERED' })
    expect(getStageAvailability('match', app, [])).toBe('not-reached')
    expect(getStageAvailability('contacts', app, [])).toBe('not-reached')
    expect(getStageAvailability('outreach', app, [])).toBe('not-reached')
  })

  it('unlocks match at MATCHED while contacts and outreach stay locked', () => {
    const app = application({ status: 'MATCHED', match_score: 0.8 })
    expect(getStageAvailability('match', app, [])).toBe('reached')
    expect(getStageAvailability('contacts', app, [])).toBe('not-reached')
    expect(getStageAvailability('outreach', app, [])).toBe('not-reached')
  })

  it('unlocks contacts at CONTACT_SEARCH, not at CONTACT_FOUND', () => {
    // Once the search is running, "no contacts" is a real provisional answer,
    // so the panel hands over to its own empty/loading handling there.
    const app = application({ status: 'CONTACT_SEARCH' })
    expect(getStageAvailability('contacts', app, [])).toBe('reached')
    expect(getStageAvailability('outreach', app, [])).toBe('not-reached')
  })

  it('unlocks outreach at OUTREACH_GENERATED', () => {
    const app = application({ status: 'OUTREACH_GENERATED' })
    expect(getStageAvailability('outreach', app, [])).toBe('reached')
  })

  it('is unknown for a manual status with no corroborating evidence', () => {
    const app = application({ status: 'WITHDRAWN' })
    expect(getStageAvailability('contacts', app, undefined)).toBe('unknown')
  })

  it('keeps a stage unlocked after a manual jump, using history', () => {
    const history = [historyEntry('CONTACT_FOUND'), historyEntry('APPLIED', 'CONTACT_FOUND')]
    const app = application({ status: 'APPLIED', match_score: 0.8 })
    expect(getStageAvailability('match', app, history)).toBe('reached')
    expect(getStageAvailability('contacts', app, history)).toBe('reached')
    // Outreach genuinely never ran — applying directly skipped it.
    expect(getStageAvailability('outreach', app, history)).toBe('not-reached')
  })
})

describe('shouldRenderStageData', () => {
  it('renders data for reached and for unknown, placeholder only for a proven negative', () => {
    expect(shouldRenderStageData('reached')).toBe(true)
    expect(shouldRenderStageData('unknown')).toBe(true)
    expect(shouldRenderStageData('not-reached')).toBe(false)
  })
})
