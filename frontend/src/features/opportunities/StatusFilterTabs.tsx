/**
 * The `/opportunities` status filter — the four-tab grouping from
 * docs/frontend/user-flows.md#application-tracker-ux, rendered as a segmented
 * control. Extracted from `OpportunitiesPage.tsx` in T7
 * (docs/frontend/frontend-revamp-spec.md) so the page file keeps only the
 * query + state and this file owns the control's presentation and keyboard
 * behavior.
 *
 * The grouping itself is **not** re-declared here: `STATUS_TABS` and
 * `matchesStatusTab` in `pipeline.ts` stay the single definition of which of
 * the 15 `ApplicationStatus` values lands in which tab, so the Dashboard's
 * pipeline counts and this control can never disagree. This component only
 * decides how the tabs look and how they respond to the keyboard.
 *
 * The count next to each label is the real number of rows in the already
 * fetched `GET /applications` response that match that tab — a
 * `filter(...).length` over data on screen, not a separate request and not an
 * invented metric (spec Section 3, item 1). It is what makes an empty tab
 * legible *before* you click it.
 *
 * Keyboard: the WAI-ARIA tabs pattern — roving `tabIndex` (only the selected
 * tab is in the tab order), Left/Right to move between tabs, Home/End to jump
 * to the ends. Activation follows focus, which is the recommended behavior
 * when switching costs nothing: filtering is a client-side partition of data
 * already in memory, with no fetch behind it.
 *
 * Owner: frontend-opportunities-agent.
 */

import { useRef, type KeyboardEvent } from 'react'
import { cn } from '@/lib/utils'
import { STATUS_TABS, type StatusTab } from './pipeline'

export type StatusTabCounts = Record<StatusTab, number>

type StatusFilterTabsProps = {
  value: StatusTab
  onChange: (tab: StatusTab) => void
  /** Row count per tab, derived from the rows already on screen. */
  counts: StatusTabCounts
  /** `id` of the region these tabs filter, for `aria-controls`. */
  panelId: string
}

export function tabId(tab: StatusTab): string {
  return `status-tab-${tab}`
}

export function StatusFilterTabs({ value, onChange, counts, panelId }: StatusFilterTabsProps) {
  const tabRefs = useRef<Partial<Record<StatusTab, HTMLButtonElement | null>>>({})

  const selectAt = (index: number) => {
    const wrapped = (index + STATUS_TABS.length) % STATUS_TABS.length
    const next = STATUS_TABS[wrapped]
    onChange(next.id)
    // Focus has to follow the selection, or the roving tabIndex would leave
    // the keyboard user's focus on a tab that's no longer in the tab order.
    tabRefs.current[next.id]?.focus()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault()
        selectAt(index + 1)
        break
      case 'ArrowLeft':
        event.preventDefault()
        selectAt(index - 1)
        break
      case 'Home':
        event.preventDefault()
        selectAt(0)
        break
      case 'End':
        event.preventDefault()
        selectAt(STATUS_TABS.length - 1)
        break
      default:
        break
    }
  }

  return (
    <div
      role="tablist"
      aria-label="Filter by status"
      aria-orientation="horizontal"
      className="flex gap-1 overflow-x-auto rounded-lg border border-gray-200 bg-gray-50 p-1 sm:inline-flex"
    >
      {STATUS_TABS.map((statusTab, index) => {
        const selected = statusTab.id === value
        return (
          <button
            key={statusTab.id}
            ref={(element) => {
              tabRefs.current[statusTab.id] = element
            }}
            type="button"
            role="tab"
            id={tabId(statusTab.id)}
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(statusTab.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={cn(
              'flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:flex-none',
              selected
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:bg-white/60 hover:text-gray-900',
            )}
          >
            {/* The explicit space keeps the computed accessible name
                "Active 2" rather than "Active2" — the count is part of what a
                screen reader announces for this tab, not decoration. */}
            {statusTab.label}{' '}
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 text-xs tabular-nums',
                selected ? 'bg-status-progress-bg text-status-progress' : 'bg-gray-200 text-gray-600',
              )}
            >
              {counts[statusTab.id]}
            </span>
          </button>
        )
      })}
    </div>
  )
}
