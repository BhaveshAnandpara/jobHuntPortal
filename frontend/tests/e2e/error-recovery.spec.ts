import { test, expect } from '@playwright/test'
import {
  createIdentity,
  openOpportunityByTitle,
  submitJobUrl,
  uploadResumeAndWaitTerminal,
  waitForOpportunityStatus,
} from './helpers'
import { JOB1_TITLE, JOB1_URL, JOB_FETCH_FAIL_URL, RESUME_B_FILE } from './fixtures'

/**
 * Error UX scenarios not already covered elsewhere (invalid URL is in
 * opportunity-flow.spec.ts; resume parse failure and replace-failure-safety
 * are in resume-management.spec.ts; the no-contacts-found empty state and
 * contact-search failure fallback are in contacts.spec.ts). Confirms: a
 * page-fetch/ingestion failure shows a normalized inline message (never a
 * raw exception), and a 409 conflict on outreach approval is handled as a
 * specific, recoverable inline message rather than a generic crash.
 */

test('a job-fetch/ingestion failure shows a normalized inline error, never a raw exception', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'errors-ingest-fail' })
  await page.goto('/')
  await page.getByLabel('Paste a job posting URL').fill(JOB_FETCH_FAIL_URL)
  await page.getByRole('button', { name: 'Submit' }).click()

  // Stays on the Dashboard (submission failed synchronously), with the
  // backend's normalized ApiError message shown inline — never a stack
  // trace, "Traceback", or raw Python exception text.
  await expect(page).toHaveURL('/')
  const errorText = page.getByText(/could not fetch|fetch failed|simulated/i)
  await expect(errorText).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Traceback')).toHaveCount(0)
  await expect(page.getByText(/File "/)).toHaveCount(0)
})

test('approve 409 conflict (already decided elsewhere) shows a specific inline message and refetches current state', async ({
  page,
}) => {
  await createIdentity(page, { emailPrefix: 'errors-409' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)
  await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)

  const reviewLink = page.getByRole('link', { name: 'Review' })
  await expect(reviewLink).toBeVisible({ timeout: 15_000 })
  await reviewLink.click()
  await expect(page).toHaveURL(/\/outreach\//)
  const outreachId = page.url().split('/outreach/')[1]

  // Decide it server-side out from under the still-open, stale UI (the
  // same real endpoint the UI itself would call, simulating "another tab
  // already approved this") — this is the real, expected concurrent-edit
  // case api-contracts.md documents for POST /outreach/{id}/approve.
  const apiBase = process.env.E2E_API_BASE_URL ?? 'http://127.0.0.1:8000'
  const directApprove = await page.request.post(`${apiBase}/outreach/${outreachId}/approve`, {
    data: { final_message: null },
  })
  expect(directApprove.ok()).toBe(true)

  // The stale UI still shows Approve/Edit/Reject (it hasn't refetched yet)
  // — clicking Approve now must hit the real 409 and show a specific
  // inline message (the backend's own message, e.g. "outreach ... is in
  // status APPROVED, not eligible for this action" — see
  // src/outreach/api/routes.py's `_conflict`), not a generic toast.
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByText(/not eligible for this action/i)).toBeVisible({ timeout: 10_000 })

  // The panel's own refetch (built into the 409 handling) then shows the
  // real current state.
  await expect(page.getByText('Approved — will be sent shortly.')).toBeVisible({ timeout: 10_000 })
})
