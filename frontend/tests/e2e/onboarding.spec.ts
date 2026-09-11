import { test, expect } from '@playwright/test'
import { createIdentity } from './helpers'

/**
 * Auth flow: no token -> /login -> register -> persisted -> protected
 * routes available; existing token -> redirected away from /register and
 * /login instead of re-registering/re-authenticating; logging out clears
 * the token and blocks protected routes again.
 */

test('no token redirects to /login, protected routes are blocked until registered', async ({ page }) => {
  await page.goto('/opportunities')
  await expect(page).toHaveURL('/login')
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
})

test('registering an account persists a token and unlocks protected routes', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'onboard' })

  // Protected routes are now reachable directly.
  await page.goto('/resumes')
  await expect(page.getByRole('heading', { name: 'Resumes' })).toBeVisible()
  await page.goto('/outreach')
  await expect(page.getByRole('heading', { name: 'Outreach Queue' })).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: 'Search Preferences' })).toBeVisible()
})

test('an existing token redirects away from /register and /login instead of re-authenticating', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'onboard-existing' })

  // Navigating back to /register or /login with a token already set
  // redirects to / rather than showing the form again.
  await page.goto('/register')
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: 'Create your account' })).toHaveCount(0)

  await page.goto('/login')
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: 'Log in' })).toHaveCount(0)
})

test('token persists across a full page reload (localStorage, not session state)', async ({ page }) => {
  await createIdentity(page, { emailPrefix: 'onboard-reload' })
  await page.reload()
  await expect(page).toHaveURL('/')
  // Proof the reload came back authenticated: `RequireIdentity` rendered the
  // protected Dashboard rather than bouncing to /login. Asserted on the page
  // heading, not on the job-URL input — T5 swaps the Dashboard's whole
  // primary-action slot for the "Add a resume to get started" onboarding CTA
  // until the account has an ACTIVE profile, and this account is brand new,
  // so the input is legitimately absent here (see jobSubmissionReadiness.ts).
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Upload a resume' })).toBeVisible()
})

test('logging out clears the token and blocks protected routes until logging back in', async ({ page }) => {
  const { email, password } = await createIdentity(page, { emailPrefix: 'onboard-logout' })

  await page.getByRole('button', { name: 'Log out' }).click()
  await expect(page).toHaveURL('/login')

  // Protected routes are blocked again — no lingering token.
  await page.goto('/opportunities')
  await expect(page).toHaveURL('/login')

  // Logging back in with the same credentials restores access. T3 made
  // `RequireIdentity` carry the bounced-off route in navigation state as
  // `from`, and `LoginPage` honors it (`resolveRedirectTarget`, same-origin
  // root-relative paths only), so sign-in returns the user to the protected
  // route they were blocked from — /opportunities here — instead of always
  // dumping them on the dashboard. Landing back on the route that was blocked
  // a moment ago is the stronger proof that access was actually restored.
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page).toHaveURL('/opportunities')
  await expect(page.getByRole('heading', { name: 'Opportunities' })).toBeVisible()
})
