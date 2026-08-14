/**
 * Plain (non-component) identity primitives, split out from
 * `IdentityProvider.tsx` so that file can stay component-only (Vite Fast
 * Refresh / oxlint's `react/only-export-components` rule). See
 * `IdentityProvider.tsx`'s header for the full rationale on why this
 * exists at all.
 *
 * Owner: frontend-shell-agent.
 */

import { createContext, useContext } from 'react'

const STORAGE_KEY = 'jobhunt.userId'

export function getCurrentUserId(): string | null {
  return localStorage.getItem(STORAGE_KEY)
}

export function setCurrentUserId(userId: string): void {
  localStorage.setItem(STORAGE_KEY, userId)
}

export function clearCurrentUserId(): void {
  localStorage.removeItem(STORAGE_KEY)
}

export type IdentityContextValue = {
  userId: string | null
  setUserId: (userId: string) => void
  clearUserId: () => void
}

export const IdentityContext = createContext<IdentityContextValue | null>(null)

/**
 * `userId` is `null` until `/welcome` creates one. Route guards (see
 * `app/RequireIdentity.tsx`) redirect to `/welcome` in that case — feature
 * pages behind the guard can assume `userId` is a non-null `string`, but
 * this hook's return type stays honest about the unguarded case for any
 * caller outside that guard (e.g. the guard itself).
 */
export function useCurrentUserId(): IdentityContextValue {
  const context = useContext(IdentityContext)
  if (context === null) {
    throw new Error('useCurrentUserId must be used within <IdentityProvider>')
  }
  return context
}
