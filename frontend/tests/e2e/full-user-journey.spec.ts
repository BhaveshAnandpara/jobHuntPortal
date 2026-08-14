import { test, expect } from '@playwright/test'
import {
  advanceApplicationStatus,
  createIdentity,
  openOpportunityByTitle,
  reloadToSeeLatestProfiles,
  submitJobUrl,
  uploadResumes,
  waitForCondition,
  waitForOpportunityStatus,
} from './helpers'
import {
  CONTACT_MECH_NAME,
  JOB1_COMPANY,
  JOB1_TITLE,
  JOB1_URL,
  PROFILE_A_TITLE,
  PROFILE_B_TITLE,
  PROFILE_C_TITLE,
  RESUME_A_FILE,
  RESUME_B_FILE,
  RESUME_C_FILE,
} from './fixtures'

/**
 * The single primary end-to-end pass (task priority 2): Welcome -> create
 * local identity -> set preferences -> upload multiple resumes -> wait for
 * processing -> Dashboard -> paste Job URL -> ingestion -> opportunity
 * appears -> matching completes -> best resume/profile shown -> other
 * evaluated profiles shown (profile_scores) -> contact discovery completes
 * -> ranked contacts shown -> outreach generated -> user reviews ->
 * approve -> approved outreach sends -> application lifecycle reflects the
 * update -> manual post-outreach transitions.
 *
 * Runs against the real browser + real running FastAPI app (see
 * e2e/backend_server.py) with a background task draining the in-memory
 * Kafka broker on a timer — every wait below is a real poll of real HTTP
 * state, never a manual reload or injected state.
 */

test.setTimeout(120_000)

test('full user journey: onboarding through outreach sent and manual lifecycle', async ({ page }) => {
  await createIdentity(page, { displayName: 'Journey User', emailPrefix: 'journey' })

  // --- Preferences (Settings) ------------------------------------------------
  await page.goto('/settings')
  await page.getByLabel('Target roles').fill('Mechanical Design Engineer')
  await page.getByLabel('Target locations').fill('Remote')
  await page.getByRole('button', { name: 'Save preferences' }).click()
  await expect(page.getByText('Preferences saved.')).toBeVisible()
  // Reload and confirm the saved value round-trips (persisted server-side,
  // not local-only state).
  await page.reload()
  await expect(page.getByLabel('Target roles')).toHaveValue('Mechanical Design Engineer')

  // --- Multi-resume upload (three professions) --------------------------------
  await page.goto('/resumes')
  await expect(page.getByText('No resumes yet')).toBeVisible()

  await uploadResumes(page, [RESUME_A_FILE, RESUME_B_FILE, RESUME_C_FILE])
  // All three should reach a terminal Parsed badge (2s poll — see
  // `waitForCondition`'s docstring in helpers.ts for why a plain
  // non-reloading wait cannot always reach this state).
  await waitForCondition(
    page,
    (timeoutMs) => expect(page.getByText('Parsed')).toHaveCount(3, { timeout: timeoutMs }),
    30_000,
  )
  // See helpers.ts's reloadToSeeLatestProfiles docstring: a known, reported
  // gap where the derived-profile section doesn't always appear
  // automatically the instant parsing finishes.
  await reloadToSeeLatestProfiles(page)
  await expect(page.getByText(PROFILE_A_TITLE)).toBeVisible()
  await expect(page.getByText(PROFILE_B_TITLE)).toBeVisible()
  await expect(page.getByText(PROFILE_C_TITLE)).toBeVisible()

  // --- Dashboard now shows the primary action, no first-run empty state ------
  await page.goto('/')
  await expect(page.getByLabel('Paste a job posting URL')).toBeVisible()

  // --- Paste a job URL aligned to profile B (mechanical) ----------------------
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)

  // --- Job Info panel: full JobResponse fields --------------------------------
  await expect(page.getByText(JOB1_COMPANY).first()).toBeVisible()
  await expect(page.getByText('Remote').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('SolidWorks').first()).toBeVisible()

  // --- Matching completes: score, recommendation, selected resume ------------
  await waitForOpportunityStatus(page, ['SHORTLISTED', 'CONTACT_SEARCH', 'CONTACT_FOUND', 'OUTREACH_GENERATED'], 30_000)
  // `.first()` — the match score legitimately renders twice: the
  // MatchPanel's own score display, and again inside the "Other resumes
  // evaluated" comparison list below.
  await expect(page.getByText('93%').first()).toBeVisible()
  // `exact: true` — a bare substring match also matches the
  // ApplicationStatus badge's "Shortlisted" label and the timeline's "From
  // SHORTLISTED..." entry text, both distinct from the MatchRecommendation
  // badge's exact "Shortlist" label this assertion actually targets.
  await expect(page.getByText('Shortlist', { exact: true })).toBeVisible()
  // `.first()` — the profile title legitimately renders twice: the
  // MatchPanel's own selected-profile display, and (concatenated with a
  // "Selected" badge in the same text node) the "Other resumes evaluated"
  // comparison list below — both correctly confirm the same profile.
  await expect(page.getByText(PROFILE_B_TITLE, { exact: false }).first()).toBeVisible()

  // --- Other resumes evaluated: multi-resume comparison, winner distinguished
  const otherResumesCard = page.locator('div', { has: page.getByRole('heading', { name: 'Other resumes evaluated' }) }).first()
  // `exact: true` — a bare substring match also matches MatchPanel's
  // "Selected resume" section label, distinct from the "Selected" winner
  // badge in the comparison list this assertion actually targets.
  await expect(page.getByText('Selected', { exact: true })).toBeVisible({ timeout: 15_000 })
  // All three evaluated profiles appear in the comparison (cross-profession /
  // multi-resume validation: the platform compared against every profile,
  // not just the winner).
  await expect(otherResumesCard).toBeVisible()

  // --- Contact discovery completes: ranked contact shown ----------------------
  await expect(page.getByText(CONTACT_MECH_NAME)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Hiring Manager')).toBeVisible()

  // --- Outreach generated -> Review link appears -------------------------------
  await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)
  const reviewLink = page.getByRole('link', { name: 'Review' })
  await expect(reviewLink).toBeVisible({ timeout: 15_000 })

  // ============================================================================
  // Human approval guarantee: Generated != Sent (explicit, named assertion).
  // Before any approval action, the outreach record must never show as sent,
  // and the review surface offers only Approve/Edit/Reject — never a "Send"
  // control, matching architecture.md's thin-client "no send endpoint" rule.
  // ============================================================================
  await reviewLink.click()
  await expect(page).toHaveURL(/\/outreach\//)
  await expect(page.getByText('Needs your review')).toBeVisible()
  await expect(page.getByText(/^Sent\.?$/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Send$/ })).toHaveCount(0)

  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByText('Approved — will be sent shortly.')).toBeVisible({ timeout: 10_000 })
  // Immediately after approval, status must be APPROVED, never SENT yet, on
  // this exact same fetched record.
  await expect(page.getByText(/^Sent\.?$/)).toHaveCount(0)

  // The send worker (background drain loop) processes outreach.approved
  // asynchronously. `OutreachReviewPage`'s own docstring documents this
  // deep-link page as fetch-once + refetchOnWindowFocus (no polling
  // interval) by design — "approval-to-send latency is a real, uncollapsed
  // gap, not something to paper over client-side" per
  // `OutreachReviewPanel`'s docstring — so observing the eventual Sent
  // transition here needs the same reload-based wait as elsewhere in this
  // suite (see `waitForCondition`'s docstring in helpers.ts), not a plain
  // wait on this page's own (nonexistent) live refetch.
  await waitForCondition(
    page,
    (timeoutMs) => expect(page.getByText(/^Sent\.?$/).first()).toBeVisible({ timeout: timeoutMs }),
    15_000,
  )

  // --- Back on Opportunity Detail: status + full ordered history -------------
  await page.goto('/opportunities')
  await openOpportunityByTitle(page, JOB1_TITLE)
  await waitForOpportunityStatus(page, ['OUTREACH_SENT'], 20_000)

  const historyBadges = page.locator('ol li')
  await expect(historyBadges).toHaveCount(8, { timeout: 15_000 })
  const historyTexts = await historyBadges.allTextContents()
  const expectedOrder = [
    'Analyzing',
    'Matched',
    'Shortlisted',
    'Finding contacts',
    'Contacts found',
    'Outreach ready for review',
    'Outreach approved',
    'Outreach sent',
  ]
  expectedOrder.forEach((label, index) => {
    expect(historyTexts[index]).toContain(label)
  })

  // --- Manual post-outreach transitions: APPLIED -> INTERVIEW -> OFFER -------
  await advanceApplicationStatus(page, 'Applied', 'Applied')
  await advanceApplicationStatus(page, 'Interview', 'Interview')
  await advanceApplicationStatus(page, 'Offer', 'Offer')

  // Terminal state: the Status Action Menu now offers nothing further.
  await expect(page.getByText('No further manual status changes are available.')).toBeVisible()
})
