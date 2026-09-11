import { test, expect } from '@playwright/test'

/**
 * Skeleton-step smoke spec — proves the Playwright config/dev-server
 * wiring works. The real golden-path specs (see
 * docs/frontend/testing-strategy.md) are written by
 * frontend-integration-ui-agent once the features they exercise exist.
 */
test('the app shell loads and redirects to /login with no token', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL('/login')
  // Scoped to the page heading: T4's `AuthShell` restyle gives the login
  // screen an `<h1>Log in</h1>` *and* a "Log in" submit button, so a bare
  // `getByText('Log in')` now resolves to two elements and fails Playwright's
  // strict mode. Both are correct UI — the locator is what needed narrowing.
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
})
