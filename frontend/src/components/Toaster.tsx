/**
 * Owner: frontend-design-agent. Mounted once, in `app/providers.tsx` —
 * feature agents import `toast` from `sonner` directly to fire a
 * notification (per docs/frontend/error-handling.md's mutation-error /
 * mutation-success rows); none mount a second `<Toaster>`.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * sonner integration (`ui/sonner`), which adds shadcn's popover/border token
 * styling and its lucide status icons on top of the same `sonner` runtime
 * this app already depended on. The exported contract is unchanged: a
 * zero-prop `<Toaster />`.
 */

import { Toaster as ShadcnToaster } from './ui/sonner'

export function Toaster() {
  return <ShadcnToaster position="top-right" richColors closeButton />
}
