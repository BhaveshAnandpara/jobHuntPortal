/**
 * User Service — see docs/frontend/api-mapping.md#user-service. Owner:
 * frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type {
  CreateUserRequest,
  UpdateUserPreferencesRequest,
  UserPreferencesResponse,
  UserResponse,
} from './types'

export function createUser(body: CreateUserRequest): Promise<UserResponse> {
  return apiClient.post<UserResponse>('/users', body)
}

export function getPreferences(userId: string): Promise<UserPreferencesResponse> {
  return apiClient.get<UserPreferencesResponse>(`/users/${userId}/preferences`)
}

export function updatePreferences(
  userId: string,
  body: UpdateUserPreferencesRequest,
): Promise<UserPreferencesResponse> {
  return apiClient.put<UserPreferencesResponse>(`/users/${userId}/preferences`, body)
}

/**
 * Fetch-once + `refetchOnWindowFocus` (TanStack's default) — preferences
 * only change in reaction to `useUpdatePreferences`, not an in-progress
 * backend process, so no polling interval per
 * docs/frontend/async-workflows.md's table.
 */
export function usePreferences(
  userId: string,
  options?: Pick<UseQueryOptions<UserPreferencesResponse, Error>, 'enabled'>,
) {
  return useQuery({
    queryKey: queryKeys.preferences(userId),
    queryFn: () => getPreferences(userId),
    enabled: Boolean(userId) && (options?.enabled ?? true),
  })
}

/**
 * Deliberately has no `onSuccess` side effect: persisting the new `id` to
 * `localStorage` and seeding `IdentityContext` (api-mapping.md's "State
 * affected" column for this action) is UI/identity composition owned by
 * `frontend-shell-agent`'s `hooks/identity.ts` — this hook only performs
 * the HTTP call and normalizes its result/error, never reaches into
 * another agent's module.
 */
export function useCreateUser() {
  return useMutation({
    mutationFn: (body: CreateUserRequest) => createUser(body),
  })
}

export function useUpdatePreferences() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      userId,
      body,
    }: {
      userId: string
      body: UpdateUserPreferencesRequest
    }) => updatePreferences(userId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.preferences(variables.userId) })
    },
  })
}
