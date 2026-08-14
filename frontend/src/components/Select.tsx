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
 */

import * as RadixSelect from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'

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
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled} name={name}>
      <RadixSelect.Trigger
        id={id}
        aria-label={rest['aria-label']}
        aria-labelledby={rest['aria-labelledby']}
        aria-invalid={invalid || undefined}
        className={`inline-flex w-full items-center justify-between gap-2 rounded-md border bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-brand disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400 data-[placeholder]:text-gray-400 ${
          invalid ? 'border-red-400' : 'border-gray-300'
        }`}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon>
          <ChevronDown className="h-4 w-4 text-gray-400" aria-hidden />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="overflow-hidden rounded-md border border-gray-200 bg-white shadow-md">
          <RadixSelect.Viewport className="p-1">
            {options.map((option) => (
              <RadixSelect.Item
                key={option.value}
                value={option.value}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-gray-900 outline-none data-[highlighted]:bg-gray-100"
              >
                <RadixSelect.ItemIndicator>
                  <Check className="h-3.5 w-3.5" aria-hidden />
                </RadixSelect.ItemIndicator>
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  )
}
