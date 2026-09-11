/**
 * Route: /outreach — see docs/frontend/routes.md#outreach--outreach-queue
 * and docs/frontend/agent-ownership.md's frontend-outreach-agent entry.
 *
 * The cross-opportunity inbox for the one mandatory, unbypassable gate in
 * this product: human approval of outreach. "Needs review" groups
 * `PENDING_APPROVAL` and `EDITED` — per
 * docs/architecture/state-machines.md's Outreach lifecycle, an `EDITED`
 * row still requires a separate approve/reject call, so it's still
 * actionable — and polls both continuously (new items can appear at any
 * time, per docs/frontend/async-workflows.md's outreach queue row).
 * "History" is a plain unfiltered fetch with no polling interval, showing
 * decided/terminal items (`APPROVED`/`REJECTED`/`SENT`/`SEND_FAILED`).
 *
 * There is deliberately no bulk "approve all" control anywhere on this
 * page, and selecting a row never mutates anything by itself — every
 * approve/edit/reject call still requires the explicit click inside
 * `OutreachReviewPanel`. See that component's header for why this is a
 * hard product requirement, not a stylistic choice.
 *
 * T9 (docs/frontend/frontend-revamp-spec.md) restyle:
 * - list + reading pane sit side by side from `lg` up (the pane sticks while
 *   the list scrolls) and stack below it, instead of the pane always being
 *   pushed under the whole table;
 * - each row carries a delivery line under its badge (`outreachCopy.ts`) so
 *   an approved row and a sent row are distinguishable at list level, not
 *   just inside the panel — the Section 2.6 invariant applies to the list too;
 * - the selected row is marked visually and for screen readers, since the
 *   pane's content otherwise has no visible origin;
 * - a 409 from inside the pane refetches *these* queries (see `onConflict`),
 *   because this page reads the record from list queries that the hooks'
 *   built-in `outreachItem` invalidation does not touch. The decided record
 *   is looked up across both tabs' data and, failing that, from the retained
 *   selection, so an item that a 409 just moved out of "Needs review" keeps
 *   its pane on screen showing the real current state rather than silently
 *   vanishing mid-decision.
 *
 * Owner: frontend-outreach-agent.
 */

import { useEffect, useMemo, useState } from 'react'
import { Inbox, ShieldCheck, X } from 'lucide-react'
import {
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  StatusBadge,
  Table,
  type TableColumn,
} from '../../components'
import { useOutreachList } from '../../api/outreach'
import { pollAlways } from '../../hooks/usePolling'
import { formatDateTime } from '../../utils/format'
import { cn } from '@/lib/utils'
import { getChannelLabel } from './channelLabel'
import { getDeliveryNote } from './outreachCopy'
import { OutreachReviewPanel } from './OutreachReviewPanel'
import type { OutreachResponse } from '../../api/types'

type QueueTab = 'needs-review' | 'history'

/** Matches routes.md's History tab definition exactly. */
const HISTORY_STATUSES = new Set<OutreachResponse['status']>(['APPROVED', 'REJECTED', 'SENT', 'SEND_FAILED'])

function truncate(text: string, max = 72): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function OutreachQueuePage() {
  const [tab, setTab] = useState<QueueTab>('needs-review')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [retainedSelection, setRetainedSelection] = useState<OutreachResponse | null>(null)

  // Both queries poll continuously — new drafts can be generated at any
  // time and this is the one page the product promises answers "what
  // needs my decision right now."
  const pendingQuery = useOutreachList('PENDING_APPROVAL', {
    refetchInterval: pollAlways(5000),
  })
  const editedQuery = useOutreachList('EDITED', {
    refetchInterval: pollAlways(5000),
  })
  // History: no interval — fetch-once + refetchOnWindowFocus (the hook's
  // default), matching async-workflows.md's "every other query" row.
  const historyQuery = useOutreachList(undefined)

  const needsReview = useMemo(() => {
    const combined = [...(pendingQuery.data ?? []), ...(editedQuery.data ?? [])]
    return combined.sort((a, b) => b.generated_at.localeCompare(a.generated_at))
  }, [pendingQuery.data, editedQuery.data])

  const historyItems = useMemo(
    () => (historyQuery.data ?? []).filter((item) => HISTORY_STATUSES.has(item.status)),
    [historyQuery.data],
  )

  const rows = tab === 'needs-review' ? needsReview : historyItems

  // Resolved across *both* tabs' data, not just the visible one: a decision
  // made elsewhere (or a 409 that revealed one) moves a record from
  // "Needs review" to "History" while its pane is open, and the pane should
  // follow the record to its new state instead of disappearing.
  const liveSelection = useMemo(
    () =>
      selectedId === null
        ? null
        : ([...needsReview, ...historyItems].find((item) => item.id === selectedId) ?? null),
    [needsReview, historyItems, selectedId],
  )

  // Last known copy of the open record, so the pane survives even the case
  // where the decided record is in neither list any more (e.g. History
  // hasn't come back yet). Never used to *stand in* for a different
  // selection — the id guard below keeps it scoped to the open row.
  useEffect(() => {
    if (liveSelection) {
      setRetainedSelection(liveSelection)
    }
  }, [liveSelection])

  const selected =
    selectedId === null
      ? null
      : (liveSelection ?? (retainedSelection?.id === selectedId ? retainedSelection : null))

  const isLoading =
    tab === 'needs-review' ? pendingQuery.isLoading || editedQuery.isLoading : historyQuery.isLoading
  const isError = tab === 'needs-review' ? pendingQuery.isError || editedQuery.isError : historyQuery.isError

  function retry() {
    if (tab === 'needs-review') {
      void pendingQuery.refetch()
      void editedQuery.refetch()
    } else {
      void historyQuery.refetch()
    }
  }

  /**
   * A 409 means this tab's copy of the record is stale. Refetch every list
   * this page renders from — including History, which is where an
   * already-decided item now belongs — so the pane redraws against the real
   * server state. This never re-issues the action that conflicted.
   */
  function refetchAfterConflict() {
    void pendingQuery.refetch()
    void editedQuery.refetch()
    void historyQuery.refetch()
  }

  function selectTab(next: QueueTab) {
    setTab(next)
    setSelectedId(null)
  }

  const columns: TableColumn<OutreachResponse>[] = [
    {
      key: 'message',
      header: 'Draft',
      cellClassName: 'max-w-md',
      render: (row) => {
        const isSelected = row.id === selectedId
        return (
          <span className="flex items-start gap-2.5">
            <span
              aria-hidden
              className={cn(
                'mt-1 h-4 w-0.5 shrink-0 rounded-full',
                isSelected ? 'bg-brand' : 'bg-transparent',
              )}
            />
            <span className={cn('text-sm', isSelected ? 'font-medium text-gray-900' : 'text-gray-700')}>
              {truncate(row.final_message ?? row.draft_message)}
            </span>
            {isSelected ? <span className="sr-only">Selected</span> : null}
          </span>
        )
      },
    },
    {
      key: 'channel',
      header: 'Channel',
      render: (row) => <span className="text-sm text-gray-600">{getChannelLabel(row.channel)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => {
        const deliveryNote = getDeliveryNote(row)
        return (
          <span className="flex flex-col items-start gap-1">
            <StatusBadge status={row.status} />
            {/*
              "Approved" and "Sent" are both positive badges; this line is
              what keeps them from reading as the same row at a glance.
            */}
            {deliveryNote ? <span className="text-xs text-gray-500">{deliveryNote}</span> : null}
          </span>
        )
      },
    },
    {
      key: 'generated',
      header: 'Generated',
      render: (row) => <span className="text-xs text-gray-500">{formatDateTime(row.generated_at)}</span>,
      hideOnMobile: true,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Outreach Queue"
        description="Every draft here was generated for you. Review each one and decide what happens to it."
      />

      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-gray-200 bg-gray-50 px-3.5 py-2.5 text-xs text-gray-600">
        <ShieldCheck className="mt-px h-4 w-4 shrink-0 text-brand" aria-hidden />
        <p>
          Nothing goes out until you approve it — there is no way to send from here. Approving hands the
          draft off; delivery happens afterwards and the status updates on its own when it does.
        </p>
      </div>

      <div
        className="mb-4 inline-flex gap-1 rounded-lg border border-gray-200 bg-gray-50 p-1"
        role="tablist"
        aria-label="Outreach queue tabs"
      >
        <Button
          type="button"
          variant={tab === 'needs-review' ? 'primary' : 'secondary'}
          role="tab"
          aria-selected={tab === 'needs-review'}
          className={cn(
            'py-1.5 shadow-none',
            tab === 'needs-review' ? '' : 'border-transparent bg-transparent text-gray-600 hover:bg-white',
          )}
          onClick={() => selectTab('needs-review')}
        >
          Needs review{needsReview.length > 0 ? ` (${needsReview.length})` : ''}
        </Button>
        <Button
          type="button"
          variant={tab === 'history' ? 'primary' : 'secondary'}
          role="tab"
          aria-selected={tab === 'history'}
          className={cn(
            'py-1.5 shadow-none',
            tab === 'history' ? '' : 'border-transparent bg-transparent text-gray-600 hover:bg-white',
          )}
          onClick={() => selectTab('history')}
        >
          History
        </Button>
      </div>

      {/*
        The reading pane lives outside the list's loading/empty/error switch
        on purpose. Deciding an item can empty the list the item came from
        (approve the last pending draft, or discover via a 409 that someone
        else already decided it) — if the pane were nested inside the
        "has rows" branch, the panel and any conflict message it is showing
        would be unmounted by that very refetch, which is exactly the silent
        failure the 409 handling exists to prevent.
      */}
      <div
        className={cn(
          'grid items-start gap-4',
          selected ? 'lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]' : 'grid-cols-1',
        )}
      >
        <div className="flex flex-col gap-2">
          {isLoading ? (
            <div className="flex flex-col gap-2" aria-busy="true">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : isError ? (
            <ErrorState message="Couldn't load the outreach queue." onRetry={retry} />
          ) : rows.length === 0 ? (
            tab === 'needs-review' ? (
              <EmptyState
                icon={<Inbox className="h-8 w-8" />}
                title="You're all caught up"
                description="No outreach is waiting on you. New drafts show up here as soon as they're generated."
              />
            ) : (
              <EmptyState
                title="No outreach history yet"
                description="Approved, rejected, and sent outreach will appear here once you've decided on something."
              />
            )
          ) : (
            <>
              <Table
                aria-label={tab === 'needs-review' ? 'Outreach needing review' : 'Outreach history'}
                columns={columns}
                rows={rows}
                getRowKey={(row) => row.id}
                onRowClick={(row) => setSelectedId(row.id === selectedId ? null : row.id)}
              />
              {selected ? null : (
                <p className="text-xs text-gray-500">Select a draft to read it in full and decide on it.</p>
              )}
            </>
          )}
        </div>
        {selected ? (
          <div className="flex flex-col gap-2 lg:sticky lg:top-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium tracking-wide text-gray-500 uppercase">Reviewing</p>
              <Button
                type="button"
                variant="secondary"
                className="border-transparent bg-transparent px-2 py-1 text-xs text-gray-500 shadow-none hover:bg-gray-100"
                onClick={() => setSelectedId(null)}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                Close review
              </Button>
            </div>
            {liveSelection === null ? (
              // The open record left the list it was selected from — it was
              // decided (here or elsewhere) and this tab no longer lists it.
              // The pane deliberately stays put showing its last known state
              // rather than vanishing mid-decision.
              <p className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">
                This draft is no longer in the current list.
              </p>
            ) : null}
            <OutreachReviewPanel key={selected.id} outreach={selected} onConflict={refetchAfterConflict} />
          </div>
        ) : null}
      </div>
    </div>
  )
}
