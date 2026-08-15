import { test, expect } from '@playwright/test'

/**
 * Skeleton-step smoke spec — proves the Playwright config/dev-server
 * wiring works. The real golden-path specs (see
 * docs/frontend/testing-strategy.md) are written by
 * frontend-integration-ui-agent once the features they exercise exist.
 */
test('the app shell loads and redirects to /login with no token', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Log in')).toBeVisible()
})
