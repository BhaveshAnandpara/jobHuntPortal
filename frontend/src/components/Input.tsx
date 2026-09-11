/**
 * Owner: frontend-design-agent. A plain, styled input — form wiring
 * (react-hook-form registration, Zod error display) is composed by the
 * form-owning feature agent, not baked in here. See
 * docs/frontend/architecture.md's stack table (React Hook Form + Zod).
 * Consumers: every feature with a form.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/input`. The exported contract is unchanged — native input attributes
 * plus this app's `invalid` flag, which drives both `aria-invalid` (what
 * shadcn's own `aria-invalid:` styles key off) and the error border.
 */

import { forwardRef, type InputHTMLAttributes } from 'react'
import { Input as ShadcnInput } from './ui/input'
import { cn } from '@/lib/utils'

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean
}

/**
 * shadcn's input is a compact h-8 control; this app's inputs have always been
 * `px-3 py-2 text-sm`. Geometry is preserved so migrating the internals is
 * not also a form-layout change on every page (see T4/T10 for restyling).
 */
const GEOMETRY_CLASSES = 'h-auto w-full rounded-md px-3 py-2 text-sm'

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid = false, className = '', ...rest },
  ref,
) {
  return (
    <ShadcnInput
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        GEOMETRY_CLASSES,
        'text-gray-900 placeholder:text-gray-400 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400',
        invalid ? 'border-red-400' : 'border-gray-300',
        className,
      )}
      {...rest}
    />
  )
})
