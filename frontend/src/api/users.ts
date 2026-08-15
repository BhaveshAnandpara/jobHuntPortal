/**
 * User Service — see docs/frontend/api-mapping.md#user-service. Owner:
 * frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type {
  CreateUserRequest,
  LoginResponse,
  UpdateUserPreferencesRequest,
  UserPreferencesResponse,
} from './types'

/** Registration — also mints a token (auto-login on account creation), so
 * this returns `LoginResponse`, not a bare `UserResponse`. */
export function createUser(body: CreateUserRequest): Promise<LoginResponse> {
  return apiClient.post<LoginResponse>('/users', body)
}

export function getPreferences(): Promise<UserPreferencesResponse> {
  return apiClient.get<UserPreferencesResponse>('/users/me/preferences')
}

export function updatePreferences(
  body: UpdateUserPreferencesRequest,
): Promise<UserPreferencesResponse> {
  return apiClient.put<UserPreferencesResponse>('/users/me/preferences', body)
}

/**
 * Fetch-once + `refetchOnWindowFocus` (TanStack's default) — preferences
 * only change in reaction to `useUpdatePreferences`, not an in-progress
 * backend process, so no polling interval per
 * docs/frontend/async-workflows.md's table.
 */
export function usePreferences(
  options?: Pick<UseQueryOptions<UserPreferencesResponse, Error>, 'enabled'>,
) {
  return useQuery({
    queryKey: queryKeys.preferences(),
    queryFn: () => getPreferences(),
    enabled: options?.enabled ?? true,
  })
}

/**
 * Deliberately has no `onSuccess` side effect: persisting the new token and
 * seeding `IdentityContext` (api-mapping.md's "State affected" column for
 * this action) is UI/identity composition owned by `frontend-shell-agent`'s
 * `hooks/identity.ts` — this hook only performs the HTTP call and
 * normalizes its result/error, never reaches into another agent's module.
 */
export function useCreateUser() {
  return useMutation({
    mutationFn: (body: CreateUserRequest) => createUser(body),
  })
}

export function useUpdatePreferences() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateUserPreferencesRequest) => updatePreferences(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.preferences() })
    },
  })
}
