/**
 * User Service — AI provider configuration (`GET/PUT /users/me/llm-config`),
 * see docs/llm-provider-config/PRODUCT.md. Split out of `api/users.ts` only
 * because it is a separate contract with a separate cache entry; it follows
 * that file's shape exactly (bare request functions + the two hooks that
 * wrap them, keys from `queryKeys`).
 *
 * Nothing here ever handles a plaintext key beyond passing it straight to
 * `PUT`: the response is the masked `UserLlmConfigResponse`, so the key the
 * caller submitted is never written back into the query cache.
 *
 * Owner: frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { UpdateUserLlmConfigRequest, UserLlmConfigResponse } from './types'

export function getLlmConfig(): Promise<UserLlmConfigResponse> {
  return apiClient.get<UserLlmConfigResponse>('/users/me/llm-config')
}

export function updateLlmConfig(
  body: UpdateUserLlmConfigRequest,
): Promise<UserLlmConfigResponse> {
  return apiClient.put<UserLlmConfigResponse>('/users/me/llm-config', body)
}

/**
 * Fetch-once, same as `usePreferences` — the config only changes in reaction
 * to `useUpdateLlmConfig`, never to a backend process, so there is no polling
 * interval (docs/frontend/async-workflows.md's table).
 *
 * A 404 means "not configured yet", which is a valid state rather than a
 * failure — it still arrives here as an `ApiError` (the client throws on
 * every non-2xx), so consumers check `toApiError(query.error).status === 404`
 * exactly as `SettingsPage` already does for preferences. The app-wide retry
 * policy (`app/queryClient.ts`) never retries a 4xx, so an unset config
 * settles immediately instead of retrying twice first.
 */
export function useLlmConfig(
  options?: Pick<UseQueryOptions<UserLlmConfigResponse, Error>, 'enabled'>,
) {
  return useQuery({
    queryKey: queryKeys.llmConfig(),
    queryFn: () => getLlmConfig(),
    enabled: options?.enabled ?? true,
  })
}

export function useUpdateLlmConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateUserLlmConfigRequest) => updateLlmConfig(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmConfig() })
    },
  })
}
