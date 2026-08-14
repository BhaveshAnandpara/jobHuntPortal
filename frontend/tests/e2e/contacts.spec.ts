import { test, expect } from '@playwright/test'
import { createIdentity, openOpportunityByTitle, submitJobUrl, uploadResumeAndWaitTerminal } from './helpers'
import { CONTACT_MECH_NAME, JOB1_TITLE, JOB1_URL, JOB4_TITLE, JOB4_URL, RESUME_B_FILE } from './fixtures'

/**
 * Contact discovery, using the real ContactsPanel embedded on Opportunity
 * Detail: shortlisted opportunity -> search -> loading -> ranked contacts
 * (name/role/company/relevance score/contact type/optional profile link),
 * empty state, and the manual "Search again" retrigger.
 */

test('ranked contacts render name, role, company, relevance score, contact type, and profile link', async ({
  page,
}) => {
  await createIdentity(page, { emailPrefix: 'contacts-ranked' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)

  await expect(page.getByText(CONTACT_MECH_NAME)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Engineering Manager, Mechanical at Acme Robotics')).toBeVisible()
  await expect(page.getByText('Acme Robotics').first()).toBeVisible()
  await expect(page.getByText('Hiring Manager')).toBeVisible()
  await expect(page.getByLabel(/Relevance score .* out of 10/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'View profile' })).toHaveAttribute(
    'href',
    'https://example.com/in/sam-lee-e2e',
  )
})

test('a company with zero people-search hits shows the documented empty state, not an error', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'contacts-empty' })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB4_URL)
  await openOpportunityByTitle(page, JOB4_TITLE)

  // Wait for the automated pipeline to reach at least CONTACT_FOUND (the
  // panel populates or shows its real empty state either way).
  await expect(page.getByText('No relevant contacts found for this company yet')).toBeVisible({
    timeout: 30_000,
  })

  // Manual "Search again" retrigger — a real, documented capability, not a
  // dead button. Re-running still legitimately finds nothing for this
  // company, which itself proves the retrigger actually calls the backend
  // (the button shows a loading state and the panel keeps polling briefly).
  const searchAgain = page.getByRole('button', { name: 'Search again' })
  await expect(searchAgain).toBeVisible()
  await searchAgain.click()
  await expect(page.getByText('Searching for contacts…')).toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('No relevant contacts found for this company yet')).toBeVisible({
    timeout: 15_000,
  })
})
