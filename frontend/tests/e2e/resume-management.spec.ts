import { test, expect } from '@playwright/test'
import {
  createIdentity,
  reloadToSeeLatestProfiles,
  replaceResumeWithFile,
  uploadResumeAndWaitTerminal,
  uploadResumes,
  waitForCondition,
} from './helpers'
import {
  PROFILE_A_TITLE,
  PROFILE_B_TITLE,
  PROFILE_C_TITLE,
  RESUME_A_FILE,
  RESUME_B_FILE,
  RESUME_C_FILE,
  RESUME_FAIL_FILE,
} from './fixtures'

/**
 * Multi-resume management: upload multiple, processing states, processed
 * resumes appear, profiles appear, multiple professions render with no
 * hard-coded assumptions, delete works.
 */

test('uploading multiple resumes across professions renders every derived profile with no hard-coded assumptions', async ({
  page,
}) => {
  await createIdentity(page, { emailPrefix: 'resumes-multi' })

  await uploadResumes(page, [RESUME_A_FILE, RESUME_B_FILE, RESUME_C_FILE])

  // See `waitForCondition`'s docstring (helpers.ts) for why a plain
  // non-reloading wait cannot always reach this state — a real, confirmed
  // backend/frontend race, not test flakiness.
  await waitForCondition(
    page,
    (timeoutMs) => expect(page.getByText('Parsed')).toHaveCount(3, { timeout: timeoutMs }),
    30_000,
  )
  await reloadToSeeLatestProfiles(page)

  // Same generic ResumesPage renders all three professions' derived fields
  // (title/seniority/summary/skills) with no per-profession branching.
  for (const title of [PROFILE_A_TITLE, PROFILE_B_TITLE, PROFILE_C_TITLE]) {
    await expect(page.getByText(title)).toBeVisible()
  }
  await expect(page.getByText('Python', { exact: true })).toBeVisible()
  await expect(page.getByText('SolidWorks', { exact: true })).toBeVisible()
  await expect(page.getByText('Talent Acquisition', { exact: true })).toBeVisible()
})

test('deleting a resume archives it — the row stays visible with an Archived badge, per the design system', async ({
  page,
}) => {
  // Note: `ResumesPage.tsx` renders every resume `GET /resumes` returns,
  // with no status filter, and `StatusBadge` has an explicit `ARCHIVED`
  // mapping — deleted resumes are meant to stay visible (audit trail),
  // matching the same "still listed, filterable, not hidden" philosophy
  // documented for `ApplicationStatus.IGNORED` in user-flows.md. So the
  // correct post-delete assertion is "shows Archived", not "disappears".
  await createIdentity(page, { emailPrefix: 'resumes-delete' })
  await uploadResumeAndWaitTerminal(page, RESUME_A_FILE)
  const row = page.locator('li', { hasText: 'resume-a-software.txt' })
  await expect(row.getByText('Parsed')).toBeVisible()

  await row.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('heading', { name: 'Delete resume' })).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()

  // See `waitForCondition`'s docstring (helpers.ts) for why a plain
  // non-reloading wait cannot always reach this state reliably.
  await waitForCondition(
    page,
    (timeoutMs) => expect(row.getByText('Archived')).toBeVisible({ timeout: timeoutMs }),
    10_000,
  )
})

test('resume parsing failure shows a terminal Parse failed badge, not a crash', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'resumes-fail' })
  await uploadResumes(page, RESUME_FAIL_FILE)

  // See `waitForCondition`'s docstring (helpers.ts) for why a plain
  // non-reloading wait cannot always reach this state.
  await waitForCondition(
    page,
    (timeoutMs) => expect(page.getByText('Parse failed')).toBeVisible({ timeout: timeoutMs }),
    20_000,
  )
  // T6 restyle: the per-row PARSE_FAILED message now also states that the
  // failure is isolated to this row, which is the property the surrounding
  // test cares about.
  await expect(
    page.getByText(
      /This resume could not be analyzed, so it has no profile\. Your other resumes are unaffected/,
    ),
  ).toBeVisible()
})

// FIXED UPSTREAM IN STEP 12 (was a permanent `test.fail()` regression
// tracker for a real unsafe-replace defect in
// frontend/src/features/resumes/ResumesPage.tsx, owned by
// frontend-profile-agent). `performReplace` now waits (via the polling
// effect driven by `useResumes`'s existing poll) for the new resume to
// reach a terminal PARSED/PARSE_FAILED status before ever deleting the
// old one — on PARSE_FAILED it shows an error toast and leaves the
// original resume alone, matching this file's own header comment ("a
// failed replacement never leaves the user with zero resumes"). Verified
// directly against the real running E2E backend before removing the
// `test.fail()` marker — this now asserts the real, correct behavior.
test('replace-failure-safety: a failed replacement should leave the original resume in place', async ({
  page,
}) => {
  await createIdentity(page, { emailPrefix: 'resumes-replace-fail' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  const originalRow = page.locator('li', { hasText: 'resume-b-mechanical.txt' })
  await expect(originalRow.getByText('Parsed')).toBeVisible()

  await replaceResumeWithFile(page, RESUME_FAIL_FILE)

  // See `waitForCondition`'s docstring (helpers.ts) for why a plain
  // non-reloading wait cannot always reach this state. Both conditions are
  // checked together within one `waitForCondition` attempt (and thus after
  // the same reload) since they describe one consistent snapshot of
  // backend state.
  await waitForCondition(
    page,
    async (timeoutMs) => {
      await expect(page.getByText('Parse failed')).toBeVisible({ timeout: timeoutMs })
      // The ORIGINAL resume's own row should still show Parsed (still
      // active), not Archived. Scoped to that specific row (not a bare
      // title-text check) because `ResumesPage.tsx` keeps archived resumes
      // visible with their derived-profile section still shown (see the
      // "deleting a resume" test above) — a plain text presence check
      // would pass even once archived, hiding a real regression.
      await expect(originalRow.getByText('Parsed')).toBeVisible({ timeout: timeoutMs })
    },
    20_000,
  )
})
