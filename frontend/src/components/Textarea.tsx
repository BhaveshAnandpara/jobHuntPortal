/**
 * Owner: frontend-design-agent. See Input.tsx's note — same conventions,
 * multi-line variant. Consumers: outreach edit, notes fields.
 */

import { forwardRef, type TextareaHTMLAttributes } from 'react'

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid = false, className = '', ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      aria-invalid={invalid || undefined}
      className={`w-full rounded-md border px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-brand disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400 ${
        invalid ? 'border-red-400' : 'border-gray-300'
      } ${className}`}
      {...rest}
    />
  )
})
