/**
 * The single definition of "has this account got a usable AI provider key?",
 * shared by the two gated actions — job submission (`jobSubmissionReadiness`,
 * which composes this with its own resume check) and resume upload
 * (`features/resumes/ResumesPage`). It lives here, outside either feature
 * folder, because both own one of the gated actions and neither owns the
 * other (docs/frontend/agent-ownership.md's cross-feature-glue rule).
 *
 * The predicate mirrors the backend gate added in Task 7 of
 * docs/llm-provider-config/TECH.md exactly: a row must exist *and* carry a
 * non-null `last_validated_at`. A saved-but-never-validated config is not
 * usable server-side, so treating it as usable here would only move the
 * rejection from a banner to a 4xx after the user has already picked a file
 * or pasted a URL. (In practice every save validates live before persisting,
 * so this is a defensive check rather than a common state.)
 *
 * "Not configured" arrives as a 404 `ApiError`, not as `data === undefined`:
 * `apiClient` throws on every non-2xx, and `GET /users/me/llm-config` uses
 * the same "unset is not an error" 404 convention as
 * `GET /users/me/preferences` — this reads it the same way
 * `LlmConfigSection` already does.
 *
 * Fails open on anything else (still loading, 5xx, network failure), for the
 * same reason `jobSubmissionReadiness` does: a slow or briefly unavailable
 * secondary API must never blank out a primary action, and the backend gate
 * is still the real authority if the user submits anyway.
 */

import { useLlmConfig } from '../../api/llmConfig'
import { toApiError } from '../../api/client'

export type LlmConfigReadiness = {
  /**
   * True only once `GET /users/me/llm-config` has definitively resolved to
   * "no validated provider key" — a 404, or a stored row that has never
   * been validated.
   */
  isBlockedOnMissingLlmConfig: boolean
}

export function useLlmConfigReadiness(): LlmConfigReadiness {
  const llmConfig = useLlmConfig()

  const queryError = llmConfig.isError ? toApiError(llmConfig.error) : undefined
  const isUnset = queryError?.status === 404
  const isUnvalidated = llmConfig.isSuccess && !llmConfig.data?.last_validated_at

  return { isBlockedOnMissingLlmConfig: isUnset || isUnvalidated }
}
