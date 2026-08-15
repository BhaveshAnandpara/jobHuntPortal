import { test, expect } from '@playwright/test'
import { createIdentity, openOpportunityByTitle, submitJobUrl, uploadResumeAndWaitTerminal } from './helpers'
import {
  INVALID_URL_INPUT,
  JOB2_TITLE,
  JOB2_URL,
  JOB3_TITLE,
  JOB3_URL,
  PROFILE_A_TITLE,
  PROFILE_C_TITLE,
  RESUME_A_FILE,
  RESUME_C_FILE,
} from './fixtures'

/**
 * Job submission + profession-independence: valid URL through opportunity
 * appearing and matching, an invalid URL producing an inline error with no
 * navigation, and a duplicate submission behaving uniformly (idempotent
 * dedup, no special-casing). Also the cross-profession validation: the
 * same routes/components handle a software-aligned and an HR-aligned
 * opportunity identically to the mechanical one already exercised in
 * full-user-journey.spec.ts.
 */

test('an invalid job URL shows an inline error and never navigates away', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'oppflow-invalid' })
  // The submit form is gated on having an active profile — see
  // JobUrlSubmitForm.tsx — so a resume upload is a required precondition
  // here even though this test is only exercising client-side URL format
  // validation, not matching.
  await uploadResumeAndWaitTerminal(page, RESUME_A_FILE)
  await page.goto('/')
  await page.getByLabel('Paste a job posting URL').fill(INVALID_URL_INPUT)
  await page.getByRole('button', { name: 'Submit' }).click()

  // Client-side URL format validation catches this before any request.
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText(/url/i)
})

test('duplicate job URL submissions are idempotent — no duplicate opportunity rows', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'oppflow-dup' })
  await uploadResumeAndWaitTerminal(page, RESUME_A_FILE)

  await submitJobUrl(page, JOB2_URL)
  await expect(page.getByText(JOB2_TITLE).first()).toBeVisible({ timeout: 20_000 })

  // Submit the exact same URL again from the Opportunities page's own copy
  // of the form.
  await page.getByLabel('Paste a job posting URL').fill(JOB2_URL)
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page).toHaveURL('/opportunities')

  // Exactly one row for this job, not two.
  await expect(page.getByText(JOB2_TITLE)).toHaveCount(1)
})

test('cross-profession: a software-aligned job selects the software profile (Profile A)', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'oppflow-swe' })
  await uploadResumeAndWaitTerminal(page, RESUME_A_FILE)

  await submitJobUrl(page, JOB2_URL)
  await openOpportunityByTitle(page, JOB2_TITLE)

  // `exact: true` — a bare substring match also matches the ApplicationStatus
  // badge's "Shortlisted" label and the timeline's "From SHORTLISTED..."
  // entry text, both distinct from the MatchRecommendation badge's exact
  // "Shortlist" label this assertion actually targets.
  await expect(page.getByText('Shortlist', { exact: true })).toBeVisible({ timeout: 30_000 })
  // `.first()` — the profile title legitimately renders twice: the
  // MatchPanel's own selected-profile display, and (concatenated with a
  // "Selected" badge in the same text node) the "Other resumes evaluated"
  // comparison list — both correctly confirm the same profile, not a bug.
  await expect(page.getByText(PROFILE_A_TITLE, { exact: false }).first()).toBeVisible()
  // `.first()` — "Python" legitimately appears in several places (the job
  // description text, the profile's skill chips, the matched-skills list),
  // any one of which confirms the software profile/skill rendered.
  await expect(page.getByText('Python').first()).toBeVisible()
})

test('cross-profession: an HR-aligned job selects the HR profile (Profile C)', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'oppflow-hr' })
  await uploadResumeAndWaitTerminal(page, RESUME_C_FILE)

  await submitJobUrl(page, JOB3_URL)
  await openOpportunityByTitle(page, JOB3_TITLE)

  // See the software-profession test above for why `exact: true` is needed.
  await expect(page.getByText('Shortlist', { exact: true })).toBeVisible({ timeout: 30_000 })
  // See the software-profession test above for why `.first()` is needed.
  await expect(page.getByText(PROFILE_C_TITLE, { exact: false }).first()).toBeVisible()
  await expect(page.getByText('Talent Acquisition').first()).toBeVisible()
})

test('the job submission form is blocked until a resume is uploaded, not left to fabricate a match', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'oppflow-noprofiles' })
  // Deliberately no resume upload. A job matched against zero ACTIVE
  // profiles is auto-IGNOREd (NO_PROFILES_AVAILABLE short-circuit in
  // workflows/langgraph/job_matching/nodes.py), and there is no retroactive
  // re-match once a resume is added later (matching/consumers.py's
  // handle_profile_updated is a deferred stub) — so a job submitted before
  // any active profile exists can never be matched. JobUrlSubmitForm blocks
  // submission entirely in this state rather than letting that happen, so
  // this test now verifies the block itself rather than a post-submission
  // "Analyzing…" state that the UI no longer allows a user to reach.
  await page.goto('/')
  await expect(page.getByLabel('Paste a job posting URL')).not.toBeVisible()
  await expect(page.getByText(/Upload a resume before adding opportunities/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Upload a resume' })).toBeVisible()
})
