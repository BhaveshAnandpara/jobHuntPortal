import { test, expect } from '@playwright/test'
import { createIdentity, uploadResumeAndWaitTerminal, uploadResumes, E2E_LLM_API_KEY } from './helpers'
import { PROFILE_A_TITLE, RESUME_A_FILE } from './fixtures'

/**
 * Per-user AI provider configuration, end to end (docs/llm-provider-config/,
 * Task 12): a brand-new account is blocked from the AI-powered actions until
 * it saves a provider key, and unblocked immediately once it has.
 *
 * This is the one spec that deliberately does NOT take `createIdentity`'s
 * default auto-configured key (`configureLlm: false`) — every other spec
 * does, because they are about what happens *after* a working key exists,
 * while this one is about the blocked state itself, which the default would
 * skip straight past.
 *
 * The key saved here is the same fake the rest of the suite uses. The save
 * goes through the real `PUT /users/me/llm-config` — real validation call,
 * real encryption, real `user_llm_config` row; only the provider's side of
 * the validation call is faked, inside the E2E backend (see
 * `E2EUserService` in e2e/backend_server.py).
 */

test('a new account is blocked from resume upload until an AI provider key is configured', async ({
  page,
}) => {
  await createIdentity(page, { emailPrefix: 'llmcfg-gate', configureLlm: false })

  // 1. Blocked: the Resumes page explains what is missing and where to fix
  // it, instead of offering an upload that would fail.
  await page.goto('/resumes')
  await expect(page.getByText('Add your AI provider key in Settings')).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByRole('link', { name: 'Go to Settings' })).toBeVisible()

  // 2. The backend gate is real, not just a UI state: uploading anyway (the
  // hidden file input is still in the DOM) is rejected with the actionable
  // message the API returns, not a crash or a silent no-op.
  await uploadResumes(page, RESUME_A_FILE)
  await expect(page.getByText(/failed to upload: Add your AI provider key in Settings/i)).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByText(/^(Parsed|Parsing)$/)).toHaveCount(0)

  // 3. Configure a key through the real Settings form.
  await page.goto('/settings')
  await expect(page.getByText(/You haven't added an AI provider key yet/i)).toBeVisible({ timeout: 15_000 })

  await page.getByRole('combobox', { name: 'AI provider' }).click()
  await page.getByRole('option', { name: 'Groq', exact: true }).click()
  await page.getByLabel('API key').fill(E2E_LLM_API_KEY)
  await page.getByRole('button', { name: 'Save AI provider' }).click()

  // Saved and verified: the masked hint proves the key was stored (and that
  // the response never echoes the key itself).
  await expect(page.getByText('AI provider saved.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/Current key \.\.\./)).toBeVisible({ timeout: 15_000 })

  // 4. Unblocked: the same upload that was rejected a moment ago now runs
  // the whole real parsing path and produces a profile.
  await page.goto('/resumes')
  await expect(page.getByText('Add your AI provider key in Settings')).toHaveCount(0)
  await uploadResumeAndWaitTerminal(page, RESUME_A_FILE)
  await expect(page.getByText('Parsed').first()).toBeVisible({ timeout: 20_000 })
  // The derived profile is what proves the key actually drove a real parse,
  // not just an accepted upload.
  await expect(page.getByText(PROFILE_A_TITLE).first()).toBeVisible({ timeout: 20_000 })
})
