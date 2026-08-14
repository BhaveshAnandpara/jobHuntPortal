/**
 * Owner: frontend-design-agent. A plain, styled input — form wiring
 * (react-hook-form registration, Zod error display) is composed by the
 * form-owning feature agent, not baked in here. See
 * docs/frontend/architecture.md's stack table (React Hook Form + Zod).
 * Consumers: every feature with a form.
 */

import { forwardRef, type InputHTMLAttributes } from 'react'

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid = false, className = '', ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={`w-full rounded-md border px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-brand disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400 ${
        invalid ? 'border-red-400' : 'border-gray-300'
      } ${className}`}
      {...rest}
    />
  )
})
