/**
 * The panel chrome every Opportunity Detail panel shares — T8
 * (docs/frontend/frontend-revamp-spec.md).
 *
 * This page has more simultaneous states than any other in the app (seven
 * panels × loading / not-yet-applicable / empty / error / populated), and the
 * spec's requirement is that those states stay *distinguishable*. Keeping the
 * heading, the optional sub-line and the not-yet-applicable treatment in one
 * component is what makes "loading", "hasn't started", and "ran and found
 * nothing" look reliably different from each other in every panel rather than
 * per-panel improvisation.
 *
 * `StageNotReached` is deliberately not `EmptyState`: an empty state means the
 * step ran and produced nothing (offer the user a next action), while this
 * means the step hasn't run (there is nothing for the user to do but wait for
 * the pipeline, so it offers no action and says what will trigger it). It is
 * also deliberately not a `Skeleton`/`Spinner`, which would claim a request is
 * in flight when none is.
 *
 * Owner: frontend-opportunities-agent.
 */

import type { ReactNode } from 'react'
import { Card, Skeleton } from '../../components'
import { cn } from '@/lib/utils'

type DetailPanelProps = {
  title: string
  /** One plain sentence about what this panel shows. Omitted where the title already says it. */
  description?: string
  /** Panel-scoped control (e.g. "Search again"), never a page-level primary action. */
  action?: ReactNode
  children: ReactNode
  className?: string
}

export function DetailPanel({ title, description, action, children, className }: DetailPanelProps) {
  return (
    <Card className={cn('p-5', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-gray-500">{description}</p> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="mt-4">{children}</div>
    </Card>
  )
}

type StageNotReachedProps = {
  icon?: ReactNode
  /** What hasn't happened yet, in the product's own words. */
  title: string
  /** What will make it happen — always a real pipeline step, never a guess at timing. */
  description: string
}

export function StageNotReached({ icon, title, description }: StageNotReachedProps) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-dashed border-gray-300 bg-gray-50 px-4 py-4">
      {icon ? (
        <span className="mt-0.5 shrink-0 text-gray-400" aria-hidden>
          {icon}
        </span>
      ) : null}
      <div className="min-w-0">
        <p className="text-sm font-medium text-gray-600">{title}</p>
        <p className="mt-0.5 text-xs text-gray-500">{description}</p>
      </div>
    </div>
  )
}

/**
 * Skeletons are `aria-hidden` by design (see components/Skeleton.tsx), so the
 * wrapper carries the announcement — the same pattern T5/T7 use on the
 * Dashboard and Opportunities list.
 */
export function PanelLoading({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-2">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className="h-4 w-full rounded-md" />
      ))}
    </div>
  )
}
