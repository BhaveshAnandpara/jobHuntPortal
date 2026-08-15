/**
 * The one client-persisted piece of auth state in this app: a JWT
 * (`jobhunt.token`, see `./identity`), issued by real
 * `POST /users`/`POST /auth/login` calls and sent as
 * `Authorization: Bearer <token>` on every request (`api/client.ts`).
 * `userId` is derived from the token client-side (display/cache-key only —
 * the backend is the sole verifier). See docs/frontend/state-management.md
 * #persisted-user-state and docs/frontend/routes.md#welcome--onboarding.
 *
 * Clears the shared React Query cache on every `setToken`/`clearToken` —
 * only one account is ever "logged in" per browser session, but without
 * this a second account signing in on the same browser would see the
 * first account's cached data until every query happened to refetch.
 *
 * Owner: frontend-shell-agent (this lives under src/hooks per
 * docs/frontend/repository-structure.md, but `app/providers.tsx` is the
 * only place `<IdentityProvider>` is mounted). Every other agent consumes
 * `useCurrentUserId()` (from `./identity`) — none reads/writes
 * `localStorage` directly.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { queryClient } from '../app/queryClient'
import { getToken, setToken as persistToken, clearToken as removeToken, decodeUserId, IdentityContext } from './identity'

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken())

  const setToken = useCallback((next: string) => {
    persistToken(next)
    setTokenState(next)
    queryClient.clear()
  }, [])

  const clearToken = useCallback(() => {
    removeToken()
    setTokenState(null)
    queryClient.clear()
  }, [])

  const userId = useMemo(() => (token ? decodeUserId(token) : null), [token])

  const value = useMemo(
    () => ({ token, userId, setToken, clearToken }),
    [token, userId, setToken, clearToken],
  )

  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>
}
