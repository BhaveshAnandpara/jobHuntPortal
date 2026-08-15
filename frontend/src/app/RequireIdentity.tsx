/**
 * Route guard: every route except `/login` and `/register` requires a
 * persisted auth token (see src/hooks/identity.ts). Redirects to `/login`
 * if none exists. An expired-but-present token is caught by the first real
 * API call's global 401 handling (`api/client.ts`), not here — this guard
 * only checks presence.
 *
 * Owner: frontend-shell-agent.
 */

import { Navigate, Outlet } from 'react-router-dom'
import { useCurrentUserId } from '../hooks/identity'

export function RequireIdentity() {
  const { token } = useCurrentUserId()

  if (token === null) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
