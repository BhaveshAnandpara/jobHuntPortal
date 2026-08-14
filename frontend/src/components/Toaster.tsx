/**
 * Owner: frontend-design-agent. Mounted once, in `app/providers.tsx` —
 * feature agents import `toast` from `sonner` directly to fire a
 * notification (per docs/frontend/error-handling.md's mutation-error /
 * mutation-success rows); none mount a second `<Toaster>`.
 */

import { Toaster as SonnerToaster } from 'sonner'

export function Toaster() {
  return <SonnerToaster position="top-right" richColors closeButton />
}
