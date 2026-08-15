/**
 * Plain (non-component) auth primitives, split out from
 * `IdentityProvider.tsx` so that file can stay component-only (Vite Fast
 * Refresh / oxlint's `react/only-export-components` rule). See
 * `IdentityProvider.tsx`'s header for the full rationale on why this
 * exists at all.
 *
 * Stores a real JWT (`jobhunt.token`), issued by `POST /users` (register)
 * or `POST /auth/login`, sent as `Authorization: Bearer <token>` on every
 * API call (see `api/client.ts`). The backend is the only verifier of this
 * token — `decodeUserId` below reads it client-side purely for display/
 * cache-key convenience, never for a security decision.
 *
 * Owner: frontend-shell-agent.
 */

import { createContext, useContext } from 'react'

const STORAGE_KEY = 'jobhunt.token'

export function getToken(): string | null {
  return localStorage.getItem(STORAGE_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(STORAGE_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(STORAGE_KEY)
}

/**
 * Reads the `sub` claim (the user id) out of a JWT's payload, without
 * verifying its signature — display/cache-key use only. The backend is the
 * sole authority on whether a token is actually valid; this never gates
 * access to anything.
 */
export function decodeUserId(token: string): string | null {
  const payload = token.split('.')[1]
  if (!payload) {
    return null
  }
  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(base64)
    const claims = JSON.parse(json) as { sub?: unknown }
    return typeof claims.sub === 'string' ? claims.sub : null
  } catch {
    return null
  }
}

export type IdentityContextValue = {
  token: string | null
  userId: string | null
  setToken: (token: string) => void
  clearToken: () => void
}

export const IdentityContext = createContext<IdentityContextValue | null>(null)

/**
 * `token` is `null` until `/register` or `/login` sets one. Route guards
 * (see `app/RequireIdentity.tsx`) redirect to `/login` in that case —
 * feature pages behind the guard can assume `token`/`userId` are non-null,
 * but this hook's return type stays honest about the unguarded case for
 * any caller outside that guard (e.g. the guard itself).
 */
export function useCurrentUserId(): IdentityContextValue {
  const context = useContext(IdentityContext)
  if (context === null) {
    throw new Error('useCurrentUserId must be used within <IdentityProvider>')
  }
  return context
}
