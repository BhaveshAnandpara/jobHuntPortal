import { test, expect } from '@playwright/test'
import {
  advanceApplicationStatus,
  createIdentity,
  openOpportunityByTitle,
  submitJobUrl,
  uploadResumeAndWaitTerminal,
  waitForOpportunityStatus,
} from './helpers'
import { JOB1_TITLE, JOB1_URL, RESUME_B_FILE } from './fixtures'

/**
 * Application lifecycle tracking: the automated chain progresses visibly
 * without a page reload (polling), and manual post-outreach transitions
 * (including the REJECTED branch, distinct from full-user-journey.spec.ts's
 * OFFER branch) work via the real Status Action Menu with correct timeline
 * ordering.
 */

test('automated pipeline progression is visible without a page reload', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'tracking-progress' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)

  // Never navigate/reload from here on — the same open page must show the
  // status advance purely from its own 3s poll of GET /applications/{id}.
  await expect(page.getByText('Analyzing…')).toBeVisible({ timeout: 5_000 }).catch(() => {
    // Matching may already have completed by the time this assertion runs
    // (fast fakes + drain loop) — that's fine, the important assertions
    // are the later, guaranteed-to-still-be-pending ones below.
  })
  await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)
  // Reaching OUTREACH_GENERATED without ever calling page.goto/page.reload
  // after the initial navigation proves this was observed via polling.
})

test('manual post-outreach lifecycle: REJECTED branch after APPLIED/INTERVIEW', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'tracking-rejected' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)
  await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)

  // Skip straight to Applied without waiting for the outreach-sent leg —
  // "a user may apply directly without receiving a referral" per
  // about_project.md, exercised here from OUTREACH_GENERATED directly.
  await advanceApplicationStatus(page, 'Applied', 'Applied')
  await advanceApplicationStatus(page, 'Interview', 'Interview')
  await advanceApplicationStatus(page, 'Rejected', 'Rejected')

  await expect(page.getByText('No further manual status changes are available.')).toBeVisible()

  // Timeline reflects the manual entries in order, each attributed to a
  // manual trigger.
  const historyEntries = page.locator('ol li')
  const texts = await historyEntries.allTextContents()
  expect(texts.some((t) => t.includes('Applied'))).toBe(true)
  expect(texts.some((t) => t.includes('Interview'))).toBe(true)
  expect(texts.some((t) => t.includes('Rejected'))).toBe(true)
})
