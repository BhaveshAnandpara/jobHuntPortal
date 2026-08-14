/**
 * The one inline field-error convention — see
 * docs/frontend/error-handling.md's `400 VALIDATION_ERROR` row ("inline
 * field-level error using `message`, form stays populated"). Every form
 * renders its react-hook-form field errors (client-side Zod) and any
 * backend-returned field-level message through this component, not ad hoc
 * `<p>` tags per form.
 *
 * Owner: frontend-design-agent.
 */

export function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null
  }
  return (
    <p role="alert" className="mt-1 text-xs text-status-negative">
      {message}
    </p>
  )
}
