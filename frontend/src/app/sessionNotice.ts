/**
 * One bit of transient, in-memory state shared between the shell's "Log out"
 * affordance (`Layout.tsx`) and the route guard (`RequireIdentity.tsx`).
 *
 * Both paths end the same way — the token goes away and the guard bounces the
 * user to `/login` — but they mean different things to the user:
 *
 *   - they clicked "Log out": leaving is the thing they just asked for, so
 *     telling them to "please sign in to continue" is noise.
 *   - anything else cleared the token (an expired/invalid session, or they
 *     deep-linked into a protected route with no session at all): the bounce
 *     needs to be *explained*, not silent — see
 *     docs/frontend/frontend-revamp-spec.md T3's "cover the session expired /
 *     redirected to login state explicitly".
 *
 * Deliberately module-scoped and not persisted: it describes a single
 * in-page navigation, and a stale flag surviving a reload would suppress a
 * notice the user does need. `consumeIntentionalSignOut()` is one-shot for
 * the same reason.
 *
 * Owner: frontend-shell-agent (T3).
 */

let intentionalSignOut = false

/** Called synchronously by the shell's Log out action, before the token is cleared. */
export function markIntentionalSignOut(): void {
  intentionalSignOut = true
}

/** Reads and clears the flag. Returns true only for a deliberate sign-out. */
export function consumeIntentionalSignOut(): boolean {
  const wasIntentional = intentionalSignOut
  intentionalSignOut = false
  return wasIntentional
}
