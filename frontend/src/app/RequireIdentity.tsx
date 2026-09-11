/**
 * Route guard: every route except `/login` and `/register` requires a
 * persisted auth token (see src/hooks/identity.ts). Redirects to `/login`
 * if none exists. An expired-but-present token is caught by the first real
 * API call's global 401 handling (`api/client.ts`), not here — this guard
 * only checks presence.
 *
 * T3 (docs/frontend/frontend-revamp-spec.md): the redirect is no longer
 * silent. Two things happen alongside it:
 *
 *   1. A toast ("Please sign in to continue") is raised through the globally
 *      mounted `<Toaster>` (app/providers.tsx), so being bounced off a
 *      protected route reads as an explained state rather than the app
 *      mysteriously landing on the login form. It is suppressed for a
 *      deliberate "Log out" click (see `./sessionNotice`), where leaving is
 *      exactly what the user asked for.
 *   2. The route the user was trying to reach is carried along in the
 *      navigation `state` as `from`, so the login screen can send them back
 *      there after a successful sign-in instead of always dumping them on
 *      the dashboard. Purely additive — a login page that ignores this state
 *      behaves exactly as before.
 *
 * The guard still renders `<Navigate>` *instead of* `<Outlet>`, never before
 * it: the protected shell/page is never mounted for an unauthenticated user,
 * so there is no flash of a half-rendered, data-less UI ahead of the
 * redirect.
 *
 * Owner: frontend-shell-agent.
 */

import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { toast } from 'sonner'
import { useCurrentUserId } from '../hooks/identity'
import { consumeIntentionalSignOut } from './sessionNotice'

/** The user-facing copy for an unauthenticated bounce. Exported for tests. */
export const SIGN_IN_REQUIRED_MESSAGE = 'Please sign in to continue'

/**
 * A stable toast id so a redirect that re-renders the guard (or two guarded
 * routes resolving in the same tick) collapses into one notice rather than
 * stacking duplicates.
 */
const SIGN_IN_TOAST_ID = 'require-identity-sign-in'

export function RequireIdentity() {
  const { token } = useCurrentUserId()
  const location = useLocation()
  const redirecting = token === null

  useEffect(() => {
    if (!redirecting) {
      // A session exists: drop any stale flag so it can't suppress a later,
      // genuine "your session ended" notice.
      consumeIntentionalSignOut()
      return
    }
    if (consumeIntentionalSignOut()) {
      return
    }
    toast.info(SIGN_IN_REQUIRED_MESSAGE, { id: SIGN_IN_TOAST_ID })
  }, [redirecting])

  if (redirecting) {
    return (
      <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />
    )
  }

  return <Outlet />
}
