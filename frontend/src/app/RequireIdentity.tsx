/**
 * Route guard: every route except `/welcome` requires a persisted
 * `user_id` (see src/hooks/identity.ts). Redirects to `/welcome` if
 * none exists — the counterpart to `/welcome` itself redirecting to `/`
 * once an identity exists (see docs/frontend/routes.md#welcome--onboarding).
 *
 * Owner: frontend-shell-agent.
 */

import { Navigate, Outlet } from 'react-router-dom'
import { useCurrentUserId } from '../hooks/identity'

export function RequireIdentity() {
  const { userId } = useCurrentUserId()

  if (userId === null) {
    return <Navigate to="/welcome" replace />
  }

  return <Outlet />
}
