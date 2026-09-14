/**
 * MatchPanel — the score, recommendation and explanation fields.
 *
 * Rendered directly with props rather than through the page + MSW: every
 * value this panel shows arrives as a prop, so a component test pins the
 * rendering rules without a network fixture standing between the assertion
 * and the branch it is about. `OpportunityDetailPage.test.tsx` already
 * covers that the props are wired from the right queries.
 *
 * The three explanation fields (`reasoning`, `hard_blocker`,
 * `insufficient_detail`) come from `useJobMatch`, are absent for every match
 * computed before they existed, and so are tested for their absent states as
 * carefully as their present ones — the regression that matters most here is
 * an empty alert box appearing on an old opportunity.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MatchPanel } from './MatchPanel'
import type { ApplicationResponse } from '../../api/types'

function applicationFixture(overrides: Partial<ApplicationResponse> = {}): ApplicationResponse {
  return {
    id: 'app-1',
    job_id: 'job-1',
    user_id: 'user-1',
    company: 'Acme Robotics',
    title: 'Senior Mechanical Design Engineer',
    status: 'SHORTLISTED',
    selected_resume_id: 'resume-1',
    match_score: 0.87,
    matched_skills: ['CAD'],
    missing_skills: ['FEA'],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as ApplicationResponse
}

function renderPanel(props: Partial<React.ComponentProps<typeof MatchPanel>> = {}) {
  return render(
    <MatchPanel
      application={applicationFixture()}
      isAvailable
      recommendation="SHORTLIST"
      isRecommendationLoading={false}
      isRecommendationError={false}
      onRetryRecommendation={vi.fn()}
      selectedProfile={undefined}
      isProfileLoading={false}
      isProfileError={false}
      onRetryProfile={vi.fn()}
      {...props}
    />,
  )
}

// ---------------------------------------------------------------------------
// reasoning
// ---------------------------------------------------------------------------

describe('reasoning', () => {
  it('shows the explanation text under its own heading', () => {
    renderPanel({
      reasoning: 'Your CAD and DFM experience covers the core responsibilities directly.',
    })

    expect(screen.getByText('Why this score')).toBeInTheDocument()
    expect(
      screen.getByText('Your CAD and DFM experience covers the core responsibilities directly.'),
    ).toBeInTheDocument()
  })

  it('renders nothing at all when it is absent', () => {
    renderPanel({ reasoning: undefined })

    expect(screen.queryByText('Why this score')).not.toBeInTheDocument()
  })

  it('renders nothing when it is null', () => {
    renderPanel({ reasoning: null })

    expect(screen.queryByText('Why this score')).not.toBeInTheDocument()
  })

  it('renders nothing when it is empty or whitespace only', () => {
    // An older record can carry "" where the field was defaulted rather than
    // filled — a heading over an empty paragraph would look broken.
    const { rerender } = renderPanel({ reasoning: '' })
    expect(screen.queryByText('Why this score')).not.toBeInTheDocument()

    rerender(
      <MatchPanel
        application={applicationFixture()}
        isAvailable
        recommendation="SHORTLIST"
        reasoning="   "
        isRecommendationLoading={false}
        isRecommendationError={false}
        onRetryRecommendation={vi.fn()}
        selectedProfile={undefined}
        isProfileLoading={false}
        isProfileError={false}
        onRetryProfile={vi.fn()}
      />,
    )
    expect(screen.queryByText('Why this score')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// hard_blocker
// ---------------------------------------------------------------------------

describe('hard blocker', () => {
  it('shows a prominent alert naming the blocker', () => {
    renderPanel({
      hardBlocker: 'Posting requires an active professional licence the profile does not list.',
    })

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Likely blocker')
    expect(alert).toHaveTextContent(
      'Posting requires an active professional licence the profile does not list.',
    )
  })

  it('is announced as an alert so it is not missed', () => {
    renderPanel({ hardBlocker: 'Onsite-only role; candidate is remote-only.' })

    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('shows no banner when there is no blocker', () => {
    renderPanel({ hardBlocker: undefined })

    expect(screen.queryByText('Likely blocker')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows no banner when it is null', () => {
    renderPanel({ hardBlocker: null })

    expect(screen.queryByText('Likely blocker')).not.toBeInTheDocument()
  })

  it('shows no banner for an empty or whitespace-only value', () => {
    // The backend normalizes these to null, but the UI must not depend on
    // that to avoid rendering an empty red box.
    renderPanel({ hardBlocker: '   ' })

    expect(screen.queryByText('Likely blocker')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not suppress the score or the skill lists', () => {
    // A blocker is additional information, not a replacement for the match
    // analysis the user came to read.
    renderPanel({ hardBlocker: 'Stated mandatory requirement the candidate fails.' })

    expect(screen.getByText('87%')).toBeInTheDocument()
    expect(screen.getByText('CAD')).toBeInTheDocument()
    expect(screen.getByText('FEA')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// insufficient_detail
// ---------------------------------------------------------------------------

describe('insufficient detail', () => {
  it('shows a subtle note when the posting was too thin to judge', () => {
    renderPanel({ insufficientDetail: true })

    expect(
      screen.getByText('Limited job detail — this score may be less reliable.'),
    ).toBeInTheDocument()
  })

  it('is not styled or announced as an error', () => {
    // The score is still real; this only qualifies it. Announcing it as an
    // alert would put it on a level with a genuine blocker.
    renderPanel({ insufficientDetail: true })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows nothing when false', () => {
    renderPanel({ insufficientDetail: false })

    expect(
      screen.queryByText('Limited job detail — this score may be less reliable.'),
    ).not.toBeInTheDocument()
  })

  it('shows nothing when absent', () => {
    renderPanel({ insufficientDetail: undefined })

    expect(
      screen.queryByText('Limited job detail — this score may be less reliable.'),
    ).not.toBeInTheDocument()
  })

  it('still shows the score it qualifies', () => {
    renderPanel({ insufficientDetail: true })

    expect(screen.getByText('87%')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// The pre-existing panel, unchanged
// ---------------------------------------------------------------------------

describe('a match with none of the three fields', () => {
  it('renders the panel cleanly, exactly as before', () => {
    // The backward-compatibility case: every opportunity matched before this
    // feature shipped. Nothing new appears, and nothing old is lost.
    renderPanel()

    expect(screen.getByText('87%')).toBeInTheDocument()
    expect(screen.getByText('Strengths')).toBeInTheDocument()
    expect(screen.getByText('CAD')).toBeInTheDocument()
    expect(screen.getByText('Gaps')).toBeInTheDocument()
    expect(screen.getByText('FEA')).toBeInTheDocument()

    expect(screen.queryByText('Why this score')).not.toBeInTheDocument()
    expect(screen.queryByText('Likely blocker')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Limited job detail — this score may be less reliable.'),
    ).not.toBeInTheDocument()
  })

  it('shows all three together when all three are present', () => {
    renderPanel({
      reasoning: 'Strong overlap on the core responsibilities.',
      hardBlocker: 'Posting states a mandatory certification the profile lacks.',
      insufficientDetail: true,
    })

    expect(screen.getByText('Strong overlap on the core responsibilities.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Likely blocker')
    expect(
      screen.getByText('Limited job detail — this score may be less reliable.'),
    ).toBeInTheDocument()
  })

  it('shows none of them before the matching stage has run', () => {
    // `isAvailable === false` short-circuits to the "not matched yet"
    // placeholder — the explanation fields must not leak past it.
    renderPanel({
      isAvailable: false,
      reasoning: 'Should not appear.',
      hardBlocker: 'Should not appear either.',
      insufficientDetail: true,
    })

    expect(screen.getByText('Not matched yet')).toBeInTheDocument()
    expect(screen.queryByText('Should not appear.')).not.toBeInTheDocument()
    expect(screen.queryByText('Likely blocker')).not.toBeInTheDocument()
  })
})
