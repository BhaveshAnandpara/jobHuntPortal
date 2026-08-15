/**
 * Auth (User Service) — see docs/frontend/api-mapping.md#user-service and
 * `api/users.ts` (registration, `POST /users`, lives there since it's the
 * same endpoint api-mapping.md documents for account creation — this file
 * only owns the login-shaped concern). Owner: frontend-api-agent.
 */

import { useMutation } from '@tanstack/react-query'
import { apiClient } from './client'
import type { LoginRequest, LoginResponse } from './types'

export function login(body: LoginRequest): Promise<LoginResponse> {
  return apiClient.post<LoginResponse>('/auth/login', body)
}

/**
 * Deliberately has no `onSuccess` side effect — same reasoning as
 * `api/users.ts`'s `useCreateUser`: persisting the token and seeding
 * `IdentityContext` is UI/identity composition the calling page (
 * `LoginPage.tsx`) owns via `useCurrentUserId().setToken`, not this hook.
 */
export function useLogin() {
  return useMutation({
    mutationFn: (body: LoginRequest) => login(body),
  })
}
