/**
 * The one client-persisted piece of state in this app: the active
 * `user_id`. There is no authentication/session endpoint on the backend
 * (confirmed — see docs/frontend/architecture.md#no-authentication-layer-yet)
 * so this is a plain localStorage value, not a real session — never treat
 * it as secure, and never build anything here that implies otherwise. See
 * docs/frontend/state-management.md#persisted-user-state and
 * docs/frontend/routes.md#welcome--onboarding.
 *
 * Owner: frontend-shell-agent (this lives under src/hooks per
 * docs/frontend/repository-structure.md, but `app/providers.tsx` is the
 * only place `<IdentityProvider>` is mounted). Every other agent consumes
 * `useCurrentUserId()` (from `./identity`) — none reads/writes
 * `localStorage` directly.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { getCurrentUserId, setCurrentUserId, clearCurrentUserId, IdentityContext } from './identity'

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [userId, setUserIdState] = useState<string | null>(() => getCurrentUserId())

  const setUserId = useCallback((next: string) => {
    setCurrentUserId(next)
    setUserIdState(next)
  }, [])

  const clearUserId = useCallback(() => {
    clearCurrentUserId()
    setUserIdState(null)
  }, [])

  const value = useMemo(
    () => ({ userId, setUserId, clearUserId }),
    [userId, setUserId, clearUserId],
  )

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>
}
