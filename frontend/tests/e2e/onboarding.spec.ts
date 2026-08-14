import { test, expect } from '@playwright/test'
import { createIdentity } from './helpers'

/**
 * Identity flow: no user_id -> /welcome -> create -> persisted -> protected
 * routes available; existing user_id -> no duplicate creation.
 */

test('no identity redirects to /welcome, protected routes are blocked until created', async ({ page }) => {
  await page.goto('/opportunities')
  await expect(page).toHaveURL('/welcome')
  await expect(page.getByRole('heading', { name: 'Welcome' })).toBeVisible()
})

test('creating an identity persists it and unlocks protected routes without a login step', async ({ page }) => {
  const { displayName } = await createIdentity(page, { emailPrefix: 'onboard' })

  // Protected routes are now reachable directly.
  await page.goto('/resumes')
  await expect(page.getByRole('heading', { name: 'Resumes' })).toBeVisible()
  await page.goto('/outreach')
  await expect(page.getByRole('heading', { name: 'Outreach Queue' })).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: 'Search Preferences' })).toBeVisible()

  // No password/login step anywhere — this is explicitly not authentication.
  await expect(page.getByText(displayName)).toHaveCount(0) // display name isn't even shown as a "logged in as" banner anywhere; sanity check it's not fabricated UI
})

test('an existing identity redirects away from /welcome instead of creating a duplicate user', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'onboard-existing' })

  // Navigating back to /welcome with an identity already set redirects to /
  // rather than showing the create-identity form again.
  await page.goto('/welcome')
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: 'Welcome' })).toHaveCount(0)
})

test('identity persists across a full page reload (localStorage, not session state)', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'onboard-reload' })
  await page.reload()
  await expect(page).toHaveURL('/')
  await expect(page.getByLabel('Paste a job posting URL')).toBeVisible()
})
