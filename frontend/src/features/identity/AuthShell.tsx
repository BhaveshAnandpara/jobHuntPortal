/**
 * T4 (docs/frontend/frontend-revamp-spec.md): the shared presentation shell
 * for the two public auth routes (`/login`, `/register`). These are the only
 * routes that render outside `app/Layout` (see `app/router.tsx`), so they own
 * their own page frame rather than inheriting the sidebar shell.
 *
 * This file is presentation only — no form wiring, no API calls. Both pages
 * keep their own `react-hook-form` + `zodResolver` setup and their own
 * mutation handling; they share only the frame, the field row, and the
 * form-level error banner so the two screens can't visually drift apart.
 *
 * Owner: frontend-identity feature. Deliberately local to
 * `src/features/identity/` rather than `src/components/`: nothing outside
 * these two routes has an auth-card layout, and promoting it prematurely
 * would add a shared contract with exactly one consumer.
 */

import type { ReactNode } from 'react'
import { Briefcase } from 'lucide-react'
import { Card, FieldError } from '../../components'

/**
 * The product wordmark shown above the card. Matches `app/Layout`'s sidebar
 * branding so signing in doesn't look like a different product.
 */
const PRODUCT_NAME = 'Career Platform'

type AuthShellProps = {
  /** Page `<h1>`. Also the accessible name e2e/unit tests query by role. */
  title: string
  /** One line of real orientation — never filler marketing copy. */
  description: string
  /** The form. */
  children: ReactNode
  /** The single cross-link to the other auth route. */
  footer: ReactNode
}

export function AuthShell({ title, description, children, footer }: AuthShellProps) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-2 pb-6 text-gray-900">
          <Briefcase className="h-5 w-5 text-brand" aria-hidden />
          <span className="text-sm font-semibold">{PRODUCT_NAME}</span>
        </div>

        <Card className="p-8">
          <h1 className="text-xl font-semibold text-gray-900">{title}</h1>
          <p className="mt-1 text-sm text-gray-500">{description}</p>
          {children}
        </Card>

        <p className="pt-6 text-center text-sm text-gray-500">{footer}</p>
      </div>
    </div>
  )
}

type AuthFieldProps = {
  /** Must match the `id` on the control rendered as `children`. */
  id: string
  label: string
  /** Rendered next to the label for genuinely optional inputs. */
  optional?: boolean
  /** Message from react-hook-form (client-side Zod). */
  error?: string
  children: ReactNode
}

/**
 * One labelled field row. The error element always carries
 * `id="<id>-error"` so the control can point `aria-describedby` at it — the
 * control stays owned by the page (it needs the `register(...)` spread), so
 * wiring that attribute is the page's job, not this component's.
 */
export function AuthField({ id, label, optional = false, error, children }: AuthFieldProps) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-gray-900">
        {label}
        {optional ? <span className="font-normal text-gray-400">{' (optional)'}</span> : null}
      </label>
      {children}
      <div id={`${id}-error`}>
        <FieldError message={error} />
      </div>
    </div>
  )
}

/**
 * Form-level (not field-level) failure — e.g. a `401 UNAUTHORIZED` from
 * `POST /auth/login`, or a `400 VALIDATION_ERROR` from `POST /users` that
 * isn't attributable to one input. Rendered inline at the top of the form,
 * not as a toast: the user is looking at the form, and per
 * docs/frontend/error-handling.md the form must stay populated so they can
 * fix and resubmit without retyping anything.
 */
export function AuthFormError({ title, message }: { title: string; message: string }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-status-negative/25 bg-status-negative-bg px-3 py-2.5"
    >
      <p className="text-sm font-medium text-status-negative">{title}</p>
      <p className="mt-0.5 text-sm text-status-negative">{message}</p>
    </div>
  )
}
