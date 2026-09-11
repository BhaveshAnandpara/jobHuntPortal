/**
 * Owner: frontend-design-agent. Radix-backed for accessible keyboard nav —
 * see docs/frontend/architecture.md's stack table ("Radix supplies
 * accessible primitives only where genuinely needed"). Feature agents
 * supply `options`; this component never fetches data itself.
 *
 * Input: `options`, `value`, `onValueChange`, optional `placeholder`,
 * `disabled`, `invalid`, `name`/`id`/`aria-label`(ledby) for form wiring.
 * Output: rendered dropdown + `onValueChange` callback.
 * Consumers: status filters, form selects (preferences, status action menu).
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/select` parts (still Radix underneath, via the unified `radix-ui`
 * package). The exported contract stays the data-driven `options` API rather
 * than shadcn's compositional `<SelectTrigger>/<SelectItem>` children — every
 * consumer passes a flat option list, and `SelectOption` is re-exported from
 * src/components/index.ts.
 */

import {
  Select as ShadcnSelect,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select'
import { cn } from '@/lib/utils'

export type SelectOption = {
  value: string
  label: string
}

type SelectProps = {
  options: SelectOption[]
  value?: string
  onValueChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
  name?: string
  id?: string
  'aria-label'?: string
  'aria-labelledby'?: string
}

export function Select({
  options,
  value,
  onValueChange,
  placeholder,
  disabled,
  invalid = false,
  name,
  id,
  ...rest
}: SelectProps) {
  return (
    <ShadcnSelect value={value} onValueChange={onValueChange} disabled={disabled} name={name}>
      <SelectTrigger
        id={id}
        aria-label={rest['aria-label']}
        aria-labelledby={rest['aria-labelledby']}
        aria-invalid={invalid || undefined}
        className={cn(
          // shadcn's trigger is `w-fit`; every consumer here places the
          // select in a full-width form row, which is the pre-shadcn
          // behavior and is preserved rather than patched at each call site.
          'h-auto w-full rounded-md bg-white px-3 py-2 text-sm text-gray-900',
          'data-placeholder:text-gray-400 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400',
          invalid ? 'border-red-400' : 'border-gray-300',
        )}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </ShadcnSelect>
  )
}
