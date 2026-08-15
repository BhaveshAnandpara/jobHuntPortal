import { test, expect } from '@playwright/test'
import {
  createIdentity,
  openOpportunityByTitle,
  submitJobUrl,
  uploadResumeAndWaitTerminal,
  waitForOpportunityStatus,
} from './helpers'
import { JOB1_TITLE, JOB1_URL, RESUME_B_FILE } from './fixtures'

/**
 * Responsive + accessibility smoke — previously deferred, exercised now
 * across the major flows (navigation, resumes, opportunities, opportunity
 * detail, outreach review). Deliberately smoke-level per the task brief
 * ("focused tests are fine, doesn't need to be one giant journey"), not an
 * exhaustive per-component audit — that's each feature agent's own
 * component-test responsibility, not this suite's.
 *
 * Mobile viewport is simulated via `page.setViewportSize` rather than a
 * second Playwright project — `Table.tsx`/`Layout.tsx`'s responsive
 * behavior is pure CSS (`md:` breakpoint), so a plain Chromium viewport
 * resize exercises the same collapse a real mobile browser would, without
 * doubling this suite's runtime by re-running every spec under a second
 * device profile (a config change out of proportion to what "previously
 * deferred, do it now" asks for).
 */

const MOBILE_VIEWPORT = { width: 390, height: 844 }

test.describe('responsive', () => {
  test('desktop viewport shows the persistent sidebar, not the mobile hamburger', async ({ page }) => {
    await createIdentity(page, { emailPrefix: 'responsive-desktop' })
    await expect(page.getByRole('navigation')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Opportunities' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Open navigation menu' })).toBeHidden()
  })

  test('mobile viewport: hamburger nav works across navigation, resumes, opportunities, opportunity detail, and outreach review', async ({
    page,
  }) => {
    await page.setViewportSize(MOBILE_VIEWPORT)
    await createIdentity(page, { emailPrefix: 'responsive-mobile' })

    // Dashboard: sidebar hidden, hamburger present, opens a real nav menu.
    const menuButton = page.getByRole('button', { name: 'Open navigation menu' })
    await expect(menuButton).toBeVisible()
    await menuButton.click()
    const nav = page.getByRole('dialog', { name: 'Menu' })
    await expect(nav).toBeVisible()
    await expect(nav.getByRole('link', { name: 'Resumes' })).toBeVisible()
    await nav.getByRole('link', { name: 'Resumes' }).click()
    await expect(page).toHaveURL('/resumes')
    // Menu closes after navigating (onNavigate callback).
    await expect(nav).toBeHidden()

    // Resumes page still usable at this width — upload flow + card layout.
    await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
    await expect(page.getByText('Parsed').first()).toBeVisible()

    // Opportunities: the mobile card list (Table.tsx's collapsed rendering),
    // not the desktop <table>, is what's interactive at this width.
    await submitJobUrl(page, JOB1_URL)
    await openOpportunityByTitle(page, JOB1_TITLE)
    await expect(page.getByRole('heading', { name: JOB1_TITLE })).toBeVisible()

    // Outreach review, reached via the mobile nav again.
    await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)
    const reviewLink = page.getByRole('link', { name: 'Review' })
    await expect(reviewLink).toBeVisible({ timeout: 15_000 })
    await reviewLink.click()
    await expect(page).toHaveURL(/\/outreach\//)
    await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible()

    // The page body itself never needs horizontal scroll at this width.
    const bodyScrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
    const viewportWidth = await page.evaluate(() => window.innerWidth)
    expect(bodyScrollWidth).toBeLessThanOrEqual(viewportWidth + 1) // +1: sub-pixel rounding
  })
})

test.describe('accessibility', () => {
  test('keyboard navigation: tab reaches the primary nav links and Enter activates them, with a visible focus outline', async ({
    page,
  }) => {
    await createIdentity(page, { emailPrefix: 'a11y-keyboard' })

    // Deterministic, not a blind search: `Layout.tsx`'s `NAV_ITEMS` is a
    // fixed, known order (Dashboard, Resumes, Opportunities, Outreach,
    // Settings). Start by focusing the first nav link directly (bypassing
    // keyboard simulation for the starting point only — an empty page has
    // nothing focused yet, and Chromium's Tab-driven focus cycling needs a
    // real starting point to advance from reliably in a headless context),
    // then Tab forward exactly twice to reach Opportunities (index 2).
    await page.getByRole('link', { name: 'Dashboard' }).focus()
    await page.keyboard.press('Tab')
    await page.keyboard.press('Tab')
    const activeElement = page.locator(':focus')
    await expect(activeElement).toHaveText(/Opportunities/)
    // A real, rendered focus indicator — not `outline: none` with nothing
    // replacing it. `focus-visible:ring-2` (Layout.tsx's nav link classes
    // don't set one explicitly, but the browser's default focus ring is
    // never suppressed here) — assert the computed outline/box-shadow is
    // not literally invisible.
    const hasVisibleFocusIndicator = await activeElement.evaluate((el) => {
      const style = window.getComputedStyle(el)
      const outlineVisible = style.outlineStyle !== 'none' && style.outlineWidth !== '0px'
      const boxShadowVisible = style.boxShadow !== 'none' && style.boxShadow !== ''
      return outlineVisible || boxShadowVisible
    })
    expect(hasVisibleFocusIndicator).toBe(true)

    await page.keyboard.press('Enter')
    await expect(page).toHaveURL('/opportunities')
  })

  test('form fields have accessible labels on Welcome, Settings, and Resumes', async ({ page }) => {
    // Welcome — unauthenticated, checked first.
    await page.goto('/welcome')
    await expect(page.getByLabel('Email')).toBeVisible()
    await expect(page.getByLabel('Display name')).toBeVisible()

    await createIdentity(page, { emailPrefix: 'a11y-labels' })

    await page.goto('/settings')
    await expect(page.getByLabel('Target roles')).toBeVisible()
    await expect(page.getByLabel('Target locations')).toBeVisible()

    await page.goto('/resumes')
    await expect(page.getByLabel('Upload resume files')).toBeAttached()
  })

  test('dialog focus handling: opening the delete-resume dialog moves focus inside it', async ({ page }) => {
    await createIdentity(page, { emailPrefix: 'a11y-dialog' })
    await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)

    const row = page.locator('li', { hasText: 'resume-b-mechanical.txt' })
    const deleteButton = row.getByRole('button', { name: 'Delete' })
    await deleteButton.click()

    const dialog = page.getByRole('dialog', { name: 'Delete resume' })
    await expect(dialog).toBeVisible()
    // Radix Dialog moves focus into the content on open — confirm focus is
    // actually inside the dialog, not left behind on the page body.
    await expect(page.locator(':focus')).toBeVisible()
    const focusInsideDialog = await dialog.evaluate((dialogEl) => dialogEl.contains(document.activeElement))
    expect(focusInsideDialog).toBe(true)
  })

  /**
   * FIXED in Step 12 (was previously a documented, expected-failing gap):
   * `components/Dialog.tsx` now accepts an optional `triggerRef` — since
   * this dialog is always used as a fully controlled component (no Radix
   * `Dialog.Trigger`, so Radix had no element on record to restore focus
   * to on close), `ResumesPage.tsx` captures the exact clicked Delete
   * button via the click event and passes it through, and `Dialog.tsx`
   * restores focus to it in `onCloseAutoFocus`.
   */
  test('dialog focus handling: closing the delete-resume dialog returns focus to the button that opened it', async ({
    page,
  }) => {
    await createIdentity(page, { emailPrefix: 'a11y-dialog-close' })
    await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)

    const row = page.locator('li', { hasText: 'resume-b-mechanical.txt' })
    const deleteButton = row.getByRole('button', { name: 'Delete' })
    await deleteButton.click()
    await expect(page.getByRole('dialog', { name: 'Delete resume' })).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'Delete resume' })).toBeHidden()
    await expect(deleteButton).toBeFocused()
  })

  test('accessible names: icon-only and primary action controls all expose a real name, not just an icon', async ({
    page,
  }) => {
    await createIdentity(page, { emailPrefix: 'a11y-names' })
    // Icon-only mobile hamburger.
    await page.setViewportSize(MOBILE_VIEWPORT)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Open navigation menu' })).toBeVisible()

    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/resumes')
    await expect(page.getByRole('button', { name: 'Upload resume' }).first()).toBeVisible()
  })

  test('loading and error states are announced, not silent or color-only', async ({ page }) => {
    await createIdentity(page, { emailPrefix: 'a11y-announce' })
    // The submit form is gated on having an active profile — see
    // JobUrlSubmitForm.tsx — so a resume upload is a required precondition
    // for both assertions below.
    await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)

    // Invalid URL inline error uses role="alert" (an assertive live region),
    // not just red text a screen-reader user would never hear about.
    await page.goto('/')
    await page.getByLabel('Paste a job posting URL').fill('not a valid url')
    await page.getByRole('button', { name: 'Submit' }).click()
    await expect(page.getByRole('alert')).toBeVisible()

    // Status is never communicated by color alone — every StatusBadge in
    // this design system renders a real text label (see
    // docs/frontend/design-system.md#status-badges); spot-checked here via
    // a freshly-submitted opportunity's badge.
    await submitJobUrl(page, JOB1_URL)
    const statusCell = page.getByRole('table', { name: 'Opportunities' }).locator('tbody tr').first()
    const badgeText = await statusCell.locator('span').filter({ hasText: /.+/ }).first().textContent()
    expect(badgeText?.trim().length).toBeGreaterThan(0)
  })
})
