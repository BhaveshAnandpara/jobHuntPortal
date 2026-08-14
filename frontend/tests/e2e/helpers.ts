import { expect, type Locator, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { uniqueEmail } from './fixtures'

/**
 * Small helpers shared across the E2E golden-path specs — deliberately not
 * a full page-object framework (over-engineering for this suite's size,
 * per the task brief's "use page objects/helpers only where they clearly
 * reduce duplication" guidance). Every helper drives the REAL UI (clicks,
 * fills, waits on real network state via visible text) — none of them
 * reach into application state directly.
 */

/**
 * KNOWN, REPORTED DEFECT worked around here (frontend/src/features/resumes/
 * ResumesPage.tsx, owned by frontend-profile-agent — out of this agent's
 * ownership to fix, see this agent's Must Not list). `handleUploadInputChange`
 * does:
 *
 *   const { files } = event.target
 *   event.target.value = ''
 *   void handleFilesSelected(files)
 *
 * Confirmed empirically (see this agent's final report) against the real
 * installed Chromium: `HTMLInputElement.files` is a LIVE collection tied to
 * the input's current selection — resetting `.value` to `''` retroactively
 * empties a PREVIOUSLY-obtained `files` reference too, not just future
 * reads. So by the time `handleFilesSelected` runs, the `fileList` it
 * receives has already been emptied by the very next line's `.value = ''`,
 * and the upload silently never happens (no fetch call, no error, no
 * toast — `handleFilesSelected`'s own `!fileList || fileList.length === 0`
 * guard just returns early). This reproduces with Playwright's own
 * `locator.setInputFiles()` (proven via instrumentation: the file count is
 * 1 when `handleUploadInputChange` reads it, and 0 immediately after
 * `event.target.value = ''` — the SAME object reference). It is not
 * automation-specific: a standalone `<input type="file">` outside this app,
 * with no CDP/Playwright involvement, exhibits the identical live-reference
 * behavior when `.value` is reset — see this agent's report for the
 * standalone repro. This means real Chromium/Edge users hitting "Upload
 * resume" may be affected in production too, not just this test suite —
 * flagged as the most severe finding in this agent's report.
 *
 * The workaround below sidesteps it without touching app code: assigning
 * `.files` via `Object.defineProperty(input, 'files', { value: ... })`
 * (a plain VALUE property) instead of the normal `input.files = dataTransfer
 * .files` assignment decouples `.files` from the native `.value` setter's
 * internal side effect, so `event.target.value = ''` no longer empties it.
 * Confirmed reliable across every spec in this suite.
 */
async function setFilesViaPinnedFileList(locator: Locator, filePaths: string[]) {
  const files = filePaths.map((path) => ({
    name: path.split(/[/\\]/).pop() ?? path,
    content: readFileSync(path, 'utf-8'),
  }))
  await locator.evaluate((el, filesToSet) => {
    const input = el as HTMLInputElement
    const dt = new DataTransfer()
    for (const file of filesToSet) {
      dt.items.add(new File([file.content], file.name, { type: 'text/plain' }))
    }
    Object.defineProperty(input, 'files', { value: dt.files, configurable: true })
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }, files)
}

/**
 * Creates a brand-new local identity via /welcome and lands on the
 * Dashboard. Returns the display name used, for later assertions.
 *
 * The two waits below use an explicit, generous timeout rather than
 * Playwright's bare 5s `expect` default — this helper runs at the start of
 * nearly every test in this suite (dozens of times per full run), and a
 * full-suite run (`npm run e2e` with no file filter) was confirmed to
 * intermittently exceed 5s here purely on Vite dev-server transform/serve
 * latency under sustained load (no app logic involved — `/welcome` is a
 * static render with no backend dependency) — reproduced by comparing an
 * isolated single-file run (always fast, well under 5s) against the same
 * spec run as part of the full suite (occasionally 5s+). A modest, targeted
 * timeout here is the right fix for that specific, real cause; it is not
 * masking a product defect.
 */
export async function createIdentity(page: Page, opts?: { displayName?: string; emailPrefix?: string }) {
  const displayName = opts?.displayName ?? 'E2E Test User'
  const email = uniqueEmail(opts?.emailPrefix ?? 'e2e')

  await page.goto('/welcome')
  await expect(page.getByRole('heading', { name: 'Welcome' })).toBeVisible({ timeout: 20_000 })
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Display name').fill(displayName)
  await page.getByRole('button', { name: 'Create identity' }).click()
  await expect(page).toHaveURL('/', { timeout: 20_000 })
  return { email, displayName }
}

/** Uploads one or more resume files via the /resumes page's hidden file
 * input (see `setFilesViaPinnedFileList`'s docstring for why this doesn't
 * use `locator.setInputFiles()` directly) and waits for at least one
 * terminal status badge to appear. */
export async function uploadResumes(page: Page, filePaths: string | string[]) {
  await page.goto('/resumes')
  const fileInput = page.getByLabel('Upload resume files')
  await setFilesViaPinnedFileList(fileInput, Array.isArray(filePaths) ? filePaths : [filePaths])
}

/**
 * KNOWN, REPORTED DEFECT worked around here — the real root cause of the
 * resume-upload flakiness this suite exhibited (confirmed by direct
 * backend probing, bypassing the browser entirely — see this agent's final
 * report for the full repro):
 *
 * 1. `src/profiles/api/dependencies.py`'s `get_session()` intentionally
 *    commits `POST /resumes`'s DB transaction only AFTER its background
 *    task (`service.run_parsing_workflow`, scheduled via `BackgroundTasks`)
 *    finishes — "so commit here covers both the upload and the parsing
 *    result together" per that file's own comment. The side effect: the
 *    newly-inserted `Resume` row is COMPLETELY INVISIBLE to any other
 *    concurrent reader (`GET /resumes`) — not merely showing `PARSING`,
 *    absent from the list entirely — for the *entire* duration of parsing,
 *    not just until the insert. Confirmed directly against the raw HTTP
 *    API: 8/8 zero-delay `GET /resumes` calls immediately following a
 *    `POST /resumes` 202 returned `[]`; the row only appears — already at
 *    its terminal status — once parsing finishes and that one transaction
 *    commits. With a real (slower) LLM this window is proportionally
 *    longer, not shorter.
 * 2. `frontend/src/api/resumes.ts`'s `allResumesTerminal` does
 *    `resumes.every(...)` — vacuously `true` for an empty array. The very
 *    first `useResumes` fetch (before any resume exists) is `[]`, so
 *    `pollUntil` reads that as "everything terminal" and never starts
 *    polling at all. If an upload's own `invalidateQueries`-triggered
 *    refetch happens to land inside window (1)'s invisibility gap (a real
 *    race, not rare — confirmed reproducing every run in this suite), the
 *    UI is left showing an empty/stale resumes list *forever*: no further
 *    `GET /resumes` is ever issued, since the interval that would
 *    otherwise catch the eventual update never started.
 *
 * Neither is fixable from here: (1) is backend source
 * (`src/profiles/api/dependencies.py`), (2) is owned by
 * frontend-api-agent (`src/api/resumes.ts`) — both out of this agent's
 * ownership (see Must Not list). The workaround mirrors this file's own
 * pre-existing `reloadToSeeLatestProfiles` pattern for a related, already-
 * documented gap: a fresh page load re-mounts `useResumes` and issues a
 * brand new initial fetch, sidestepping the permanently-stalled interval —
 * exactly what a real user stuck on a page that never updates would
 * eventually do. `waitForCondition` below gives the current page a real,
 * un-reloaded chance first (the common/fast case, where no workaround is
 * even needed), then falls back to repeated reloads — never weakens what
 * is actually asserted, only how long we're willing to wait for it to
 * become true through the real UI.
 */
export async function waitForCondition(
  page: Page,
  check: (timeoutMs: number) => Promise<void>,
  totalTimeoutMs = 20_000,
): Promise<void> {
  const deadline = Date.now() + totalTimeoutMs
  const firstAttemptBudget = Math.min(6000, totalTimeoutMs)
  try {
    await check(firstAttemptBudget)
    return
  } catch {
    // Falls through to the reload-based fallback below.
  }
  while (Date.now() < deadline) {
    await page.reload()
    try {
      await check(Math.min(3000, Math.max(0, deadline - Date.now())))
      return
    } catch {
      // Keep retrying until the deadline; the final attempt below surfaces
      // Playwright's own real, detailed assertion error.
    }
  }
  await page.reload()
  await check(3000)
}

/** Uploads one resume file and waits for it to reach a terminal status
 * badge (Parsed or Parse failed). */
export async function uploadResumeAndWaitTerminal(
  page: Page,
  filePath: string,
  opts?: { timeoutMs?: number },
) {
  await uploadResumes(page, filePath)
  // The row for the just-uploaded file shows a status badge that starts at
  // Uploaded/Parsing and settles to Parsed or Parse failed (2s poll per
  // docs/frontend/async-workflows.md — see `waitForCondition`'s docstring
  // for why a plain non-reloading wait cannot always reach that state).
  // Wait for either terminal badge to appear anywhere on the page rather
  // than a specific row (multiple resumes may be present from earlier
  // steps in the same test).
  await waitForCondition(
    page,
    (timeoutMs) =>
      expect(page.getByText(/^(Parsed|Parse failed)$/).first()).toBeVisible({ timeout: timeoutMs }),
    opts?.timeoutMs ?? 20_000,
  )
  await reloadToSeeLatestProfiles(page)
}

/**
 * KNOWN, REPORTED DEFECT worked around here (frontend/src/api/resumes.ts's
 * `useUploadResume` + frontend/src/features/resumes/ResumesPage.tsx, both
 * owned by other agents — out of this agent's ownership to fix). Confirmed
 * against the real running E2E backend: `useUploadResume`'s `onSuccess`
 * invalidates the `profiles` query key exactly once, immediately after the
 * upload's `202 Accepted` response — well before the background parsing
 * workflow has actually created the `CandidateProfile` row. `useProfiles`
 * has no `refetchInterval` (fetch-once + `refetchOnWindowFocus`, matching
 * every other non-progress query per docs/frontend/async-workflows.md), and
 * nothing re-invalidates `profiles` once the resume's own `resumes` poll
 * later reaches `PARSED`. Net effect: the Resumes page can show a
 * permanent "Parsed" badge with no derived-profile section underneath it,
 * until something else (a manual reload, or the window regaining focus)
 * happens to trigger a fresh `GET /profiles` — reproduced reliably in this
 * suite (a resume uploaded after others in the same batch consistently
 * shows Parsed with no profile section until reloaded). A real reload is
 * exactly what a real user might eventually do, but the UX gap (no
 * automatic progressive disclosure once parsing finishes, contradicting
 * docs/frontend/async-workflows.md's resume-parsing diagram) is real and
 * worth fixing centrally (e.g. `useProfiles` polling like `useResumes`
 * does, or re-invalidating `profiles` when `useResumes` observes a
 * resume's status become `PARSED`) — see this agent's final report.
 */
export async function reloadToSeeLatestProfiles(page: Page) {
  await page.reload()
}

/** Uploads a replacement resume via the /resumes page's "Replace" flow
 * (click Replace on a row, then choose a file for the resulting hidden
 * single-file input) — same pinned-FileList technique as `uploadResumes`. */
export async function replaceResumeWithFile(page: Page, filePath: string) {
  await page.getByRole('button', { name: 'Replace' }).click()
  const fileInput = page.getByLabel('Replacement resume file')
  await setFilesViaPinnedFileList(fileInput, [filePath])
}

/** Submits a job URL from the Dashboard's primary form and lands on
 * /opportunities. */
export async function submitJobUrl(page: Page, url: string) {
  await page.goto('/')
  await page.getByLabel('Paste a job posting URL').fill(url)
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page).toHaveURL('/opportunities')
}

/**
 * From /opportunities, clicks the row whose title matches `titleSubstring`
 * and waits for the Opportunity Detail page to load. Polls the list (5s
 * interval) up to `timeoutMs` for the row to appear at all.
 *
 * KNOWN, REPORTED DEFECT worked around here — a real, confirmed TEST
 * defect this agent's own earlier draft had (see this agent's final
 * report): `OpportunitiesPage.tsx` shows a transient, non-interactive
 * "{company} — {title} submitted. Analyzing…" banner (a plain `<div>`,
 * no click handler, no `role="button"`) containing this SAME title text,
 * from the moment of submission until the real `Application` row for that
 * job appears in `GET /applications`. A bare `page.getByText(titleSubstring)
 * .first()` can match that banner (it renders before the table in DOM
 * order whenever the real row hasn't appeared yet) instead of the real
 * row — `.click()` then silently does nothing (no navigation, no error),
 * confirmed via direct reproduction. `Table.tsx` gives each real row
 * (desktop `<tr>` or the collapsed mobile `<div>`, whichever the current
 * viewport shows) `role="button"`, which the banner never has, so a
 * role-based locator unambiguously targets only the real, clickable row.
 */
export async function openOpportunityByTitle(page: Page, titleSubstring: string, timeoutMs = 20_000) {
  const row = page.getByRole('button', { name: titleSubstring })
  await expect(row).toBeVisible({ timeout: timeoutMs })
  await row.click()
  await expect(page.getByRole('heading', { name: titleSubstring })).toBeVisible()
}

/**
 * Opens the Opportunity Detail page's Status Action Menu (a Radix `Select`)
 * and picks `optionLabel`, retrying the open-click if the option doesn't
 * appear promptly. `Select`'s trigger/content re-render whenever
 * `currentStatus` changes (`StatusActionMenu.tsx` resets its own selection
 * via a `useEffect` keyed on it), and this page's own `applicationQuery`
 * polls every 3s — so a trigger click that happens to land in the same
 * tick as one of those re-renders can occasionally open (or fail to open)
 * a dropdown whose content then never stabilizes for Playwright's
 * actionability check, which otherwise waits indefinitely (observed once
 * as a full 120s test-timeout hang in a full-suite run, never in isolated
 * runs — consistent with this being timing-sensitive under load, not a
 * deterministic bug). Bounding each attempt and retrying the trigger click
 * turns that hang into a bounded, real retry against a genuinely
 * interactive element, without weakening what's ultimately asserted.
 */
export async function advanceApplicationStatus(
  page: Page,
  optionLabel: string,
  expectedLabel: string,
  timeoutMs = 20_000,
) {
  const deadline = Date.now() + timeoutMs
  const option = page.getByRole('option', { name: optionLabel })
  for (;;) {
    await page.getByRole('combobox', { name: 'New status' }).click()
    try {
      await option.waitFor({ state: 'visible', timeout: Math.min(3000, Math.max(0, deadline - Date.now())) })
      break
    } catch (error) {
      if (Date.now() >= deadline) throw error
      await page.keyboard.press('Escape').catch(() => {})
    }
  }
  await option.click()
  await page.getByRole('button', { name: 'Update' }).click()
  await expect(page.locator('main').getByText(expectedLabel, { exact: true }).first()).toBeVisible({
    timeout: 10_000,
  })
}

/** `ApplicationStatus` -> the exact visible `StatusBadge` label, mirroring
 * frontend/src/utils/status.ts's STATUS_PRESENTATION table (kept in sync by
 * hand — a duplicate mapping here, not an import, since Playwright specs
 * assert on rendered text as a real user would see it, deliberately not
 * importing app source into the test). */
export const APPLICATION_STATUS_LABEL: Record<string, string> = {
  DISCOVERED: 'Analyzing…',
  MATCHED: 'Matched',
  SHORTLISTED: 'Shortlisted',
  CONTACT_SEARCH: 'Finding contacts…',
  CONTACT_FOUND: 'Contacts found',
  OUTREACH_GENERATED: 'Outreach ready for review',
  OUTREACH_APPROVED: 'Outreach approved',
  OUTREACH_SENT: 'Outreach sent',
  REFERRED: 'Referred',
  APPLIED: 'Applied',
  INTERVIEW: 'Interview',
  OFFER: 'Offer',
  REJECTED: 'Rejected',
  IGNORED: 'Ignored',
  WITHDRAWN: 'Withdrawn',
}

/** Waits for the Opportunity Detail page's status badge (in the page
 * header) to read one of `statuses` (ApplicationStatus enum values),
 * polling via the page's own real 3s poll — this helper just waits on the
 * visible label text, no manual reload. */
export async function waitForOpportunityStatus(page: Page, statuses: string[], timeoutMs = 40_000) {
  const labels = statuses.map((s) => APPLICATION_STATUS_LABEL[s] ?? s)
  const pattern = new RegExp(`^(${labels.map(escapeRegExp).join('|')})$`)
  await expect(page.locator('main').getByText(pattern).first()).toBeVisible({ timeout: timeoutMs })
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
