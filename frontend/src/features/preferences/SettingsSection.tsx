/**
 * The two layout primitives every form section on `/settings` is built from.
 *
 * Extracted from `SettingsPage.tsx` unchanged when the AI-provider section
 * arrived (docs/llm-provider-config/TECH.md, Task 9): that page owns exactly
 * the six `UpdateUserPreferencesRequest` fields and nothing else, so the new
 * section is a sibling form rather than more rows in the preferences form —
 * and two sibling forms need one shared `Section`/`Field` rather than a
 * duplicated pair, or an import reaching into a page module.
 *
 * These are presentation only: neither knows about react-hook-form, zod, or
 * any request shape. Each form wires its own controls through `Field`'s
 * render prop.
 *
 * Owner: frontend-profile-agent.
 */

import type { ReactNode } from 'react'
import { Card, FieldError } from '../../components'

export function Section({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <Card className="p-0">
      <fieldset className="flex flex-col">
        <legend className="sr-only">{title}</legend>
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          <p className="mt-0.5 text-xs text-gray-500">{description}</p>
        </div>
        <div className="flex flex-col gap-5 px-6 py-5">{children}</div>
      </fieldset>
    </Card>
  )
}

/**
 * One labelled form row. The control is a render prop so the field owns the
 * `id`/`aria-describedby`/`invalid` wiring instead of every call site
 * re-deriving the same three ids by hand.
 */
export function Field({
  id,
  label,
  hint,
  optional = false,
  badge,
  error,
  children,
}: {
  id: string
  label: string
  hint?: string
  optional?: boolean
  badge?: string | null
  error?: string
  children: (control: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode
}) {
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium text-gray-900">
          {label}
          {optional ? <span className="ml-1 font-normal text-gray-400">(optional)</span> : null}
        </label>
        {badge ? <span className="shrink-0 text-xs text-gray-500">{badge}</span> : null}
      </div>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint ? (
        <p id={hintId} className="text-xs text-gray-500">
          {hint}
        </p>
      ) : null}
      {/* `FieldError` is the app-wide inline validation convention (see
          error-handling.md); it's wrapped here only to give the message an id
          the control can point at via `aria-describedby`. */}
      {error ? (
        <div id={errorId}>
          <FieldError message={error} />
        </div>
      ) : null}
    </div>
  )
}
