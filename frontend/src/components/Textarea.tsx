/**
 * Owner: frontend-design-agent. See Input.tsx's note — same conventions,
 * multi-line variant. Consumers: outreach edit, notes fields.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/textarea`; the exported contract (native textarea attributes plus
 * `invalid`) is unchanged.
 */

import { forwardRef, type TextareaHTMLAttributes } from 'react'
import { Textarea as ShadcnTextarea } from './ui/textarea'
import { cn } from '@/lib/utils'

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid = false, className = '', ...rest },
  ref,
) {
  return (
    <ShadcnTextarea
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        // Geometry preserved from the pre-shadcn textarea so existing forms
        // (outreach edit in particular) keep their current sizing.
        'w-full rounded-md px-3 py-2 text-sm',
        'text-gray-900 placeholder:text-gray-400 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400',
        invalid ? 'border-red-400' : 'border-gray-300',
        className,
      )}
      {...rest}
    />
  )
})
