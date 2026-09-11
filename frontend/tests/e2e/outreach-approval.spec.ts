import { test, expect } from '@playwright/test'
import {
  createIdentity,
  openOpportunityByTitle,
  submitJobUrl,
  uploadResumeAndWaitTerminal,
  waitForCondition,
  waitForOpportunityStatus,
} from './helpers'
import { CONTACT_MECH_NAME, JOB1_TITLE, JOB1_URL, RESUME_B_FILE } from './fixtures'

/**
 * Outreach human-approval gate — the three real decision paths, driven
 * entirely from the /outreach queue (not the Opportunity Detail deep
 * link, which full-user-journey.spec.ts already exercises), plus the
 * explicit Generated != Sent guarantee for each path.
 */

async function setUpPendingOutreach(page: import('@playwright/test').Page, emailPrefix: string) {
  await createIdentity(page, { emailPrefix })
  await uploadResumeAndWaitTerminal(page, RESUME_B_FILE)
  await submitJobUrl(page, JOB1_URL)
  await openOpportunityByTitle(page, JOB1_TITLE)
  await waitForOpportunityStatus(page, ['OUTREACH_GENERATED'], 30_000)

  await page.goto('/outreach')
  await expect(page.getByText('Needs review (1)')).toBeVisible({ timeout: 10_000 })
  // Role-based, not a bare text locator — the draft's row (desktop `<tr>`
  // or the collapsed mobile `<div>`, per `Table.tsx`) carries `role="button"`
  // and its accessible name includes the draft text (which contains the
  // contact's name); this avoids ever depending on which of the two
  // parallel desktop/mobile row renderings `.first()` would otherwise pick.
  await page.getByRole('button', { name: CONTACT_MECH_NAME }).click()
}

test('reject path: generated -> reject -> rejected, never sent, removed from the pending queue', async ({
  page,
}) => {
  await setUpPendingOutreach(page, 'outreach-reject')

  await page.getByRole('button', { name: 'Reject' }).click()
  await expect(page.getByRole('heading', { name: 'Reject this outreach?' })).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: 'Reject' }).click()

  // Rejecting moves the record out of PENDING_APPROVAL/EDITED, so
  // `OutreachQueuePage`'s own selection logic (`rows.find(item => item.id
  // === selectedId)`) no longer finds it once the resulting `outreachList`
  // invalidation refetches, and the "Needs review" list empties out to its
  // empty state. (T9 note: the reading pane itself deliberately stays
  // mounted showing the record's last known state rather than vanishing
  // mid-decision — see `OutreachQueuePage`'s comment on why the pane sits
  // outside the list's loading/empty/error switch. The emptied *list* is
  // the signal asserted here.) The terminal REJECTED status itself is
  // confirmed via the History tab below.
  await expect(page.getByText("You're all caught up")).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/^Sent\.?$/)).toHaveCount(0)

  // Wait a few drain-loop cycles to prove nothing sends a rejected draft.
  await page.waitForTimeout(2000)
  await expect(page.getByText(/^Sent\.?$/)).toHaveCount(0)

  await page.getByRole('tab', { name: 'History' }).click()
  await expect(page.getByText('Rejected').first()).toBeVisible()
})

test('edit path: generated -> edit -> save -> still not sent, still pending decision', async ({ page }) => {
  await setUpPendingOutreach(page, 'outreach-edit')

  await page.getByRole('button', { name: 'Edit' }).click()
  const textarea = page.getByRole('textbox')
  await textarea.fill('A manually edited outreach message for E2E verification.')
  await page.getByRole('button', { name: 'Save edit' }).click()

  await expect(page.getByText('Edited, needs your review').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/^Sent\.?$/)).toHaveCount(0)
  // Scoped to the panel's own message paragraph, not a bare text locator —
  // once edited, the same text also appears in the queue table/card row's
  // "Draft" column (which now shows `final_message`), so a bare
  // `getByText(...)` matches both and is a Playwright strict-mode
  // violation. The panel renders the message in a `<p>`; the row renders
  // it in a `<td>`/`<span>` (per `Table.tsx`'s desktop/mobile split).
  await expect(
    page.getByRole('paragraph').filter({ hasText: 'A manually edited outreach message for E2E verification.' }),
  ).toBeVisible()

  // Still actionable after edit — approve/reject remain available, edit
  // alone never sends.
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible()
})

test('approve path: generated -> approve -> approved -> sent (background dispatcher only)', async ({ page }) => {
  await setUpPendingOutreach(page, 'outreach-approve')

  await expect(page.getByText('Needs your review').first()).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).click()

  // Approving also moves the record out of PENDING_APPROVAL/EDITED, so —
  // same reasoning as the reject path above — the "Needs review" list
  // empties out to its empty state. (full-user-journey.spec.ts's
  // deep-link-page test already proves the "Approved — will be sent
  // shortly." intermediate-state text renders correctly, and proves the
  // precise Generated-never-Sent-synchronously ordering, on a page whose
  // query isn't status-filtered the way this queue is.) The meaningful
  // guarantee left to verify from this page: the background dispatcher —
  // never the approval request/response itself — is what eventually moves
  // it to Sent.
  await expect(page.getByText("You're all caught up")).toBeVisible({ timeout: 10_000 })

  // History has no live polling (fetch-once + refetchOnWindowFocus) — see
  // `waitForCondition`'s docstring in helpers.ts for the same
  // reload-based-wait rationale applied here. The History tab selection is
  // local component state (`OutreachQueuePage`'s `useState<QueueTab>`), so
  // every reload `waitForCondition` performs drops back to "Needs review" —
  // re-select the tab inside the check rather than once before it, or the
  // SENT row simply isn't on screen to be found on any attempt after the
  // first.
  await waitForCondition(
    page,
    async (timeoutMs) => {
      await page.getByRole('tab', { name: 'History' }).click()
      await expect(page.getByText('Sent').first()).toBeVisible({ timeout: timeoutMs })
    },
    15_000,
  )
})
