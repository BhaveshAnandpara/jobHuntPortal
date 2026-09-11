/**
 * See docs/frontend/design-system.md#primary-action-emphasis — exactly one
 * `primary` button visible per screen at a time is a page-authoring
 * convention, not something this component enforces itself.
 *
 * Owner: frontend-design-agent. Input: props below. Output: a styled
 * `<button>`. Consumers: every feature.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): the internals are now shadcn's
 * `ui/button` (cva-driven variants, focus-visible ring, disabled handling).
 * The exported prop contract is unchanged — this app's three semantic
 * variants (`primary`/`secondary`/`destructive`) and `isLoading` are mapped
 * onto shadcn's variant set rather than exposing shadcn's own variant names
 * to feature code.
 */

import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { Loader2 } from 'lucide-react'
import { Button as ShadcnButton } from './ui/button'
import { cn } from '@/lib/utils'

type Variant = 'primary' | 'secondary' | 'destructive'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  isLoading?: boolean
}

/**
 * Which shadcn variant each JobHunt variant builds on. `secondary` and
 * `destructive` are both bordered-on-white buttons here, so both start from
 * shadcn's `outline`; only the foreground/border color differs.
 */
const SHADCN_VARIANT: Record<Variant, 'default' | 'outline'> = {
  primary: 'default',
  secondary: 'outline',
  destructive: 'outline',
}

/**
 * The brand/semantic colors layered over the shadcn variant. Kept as the
 * existing token classes (`bg-brand`, `hover:bg-brand-hover`) so the palette
 * still comes from the `@theme` block in src/index.css.
 */
const VARIANT_CLASSES: Record<Variant, string> = {
  primary: 'bg-brand text-white hover:bg-brand-hover',
  secondary: 'bg-white text-gray-900 border-gray-300 hover:bg-gray-50',
  destructive: 'bg-white text-red-600 border-red-300 hover:bg-red-50',
}

/**
 * shadcn's default size is a compact h-8 control; this app's buttons have
 * always been `px-4 py-2` with an `md` radius and pages are laid out around
 * that. Sizing/geometry is preserved here so migrating the internals is not
 * also a silent layout change across every screen — restyling geometry is
 * the job of the later per-page tickets (T3–T10).
 */
const GEOMETRY_CLASSES = 'h-auto rounded-md px-4 py-2 text-sm font-medium'

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', isLoading = false, disabled, className = '', children, type = 'button', ...rest },
  ref,
) {
  return (
    <ShadcnButton
      ref={ref}
      type={type}
      variant={SHADCN_VARIANT[variant]}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      className={cn(GEOMETRY_CLASSES, VARIANT_CLASSES[variant], className)}
      {...rest}
    >
      {isLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
      {children}
    </ShadcnButton>
  )
})
