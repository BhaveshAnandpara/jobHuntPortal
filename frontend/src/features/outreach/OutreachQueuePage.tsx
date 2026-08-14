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
 * Owner: frontend-outreach-agent.
 */

import { useMemo, useState } from 'react'
import { Inbox } from 'lucide-react'
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
import { useCurrentUserId } from '../../hooks/identity'
import { pollAlways } from '../../hooks/usePolling'
import { formatDateTime } from '../../utils/format'
import { getChannelLabel } from './channelLabel'
import { OutreachReviewPanel } from './OutreachReviewPanel'
import type { OutreachResponse } from '../../api/types'

type QueueTab = 'needs-review' | 'history'

/** Matches routes.md's History tab definition exactly. */
const HISTORY_STATUSES = new Set<OutreachResponse['status']>(['APPROVED', 'REJECTED', 'SENT', 'SEND_FAILED'])

function truncate(text: string, max = 72): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function OutreachQueuePage() {
  const { userId } = useCurrentUserId()
  const activeUserId = userId ?? ''
  const [tab, setTab] = useState<QueueTab>('needs-review')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Both queries poll continuously — new drafts can be generated at any
  // time and this is the one page the product promises answers "what
  // needs my decision right now."
  const pendingQuery = useOutreachList(activeUserId, 'PENDING_APPROVAL', {
    refetchInterval: pollAlways(5000),
  })
  const editedQuery = useOutreachList(activeUserId, 'EDITED', {
    refetchInterval: pollAlways(5000),
  })
  // History: no interval — fetch-once + refetchOnWindowFocus (the hook's
  // default), matching async-workflows.md's "every other query" row.
  const historyQuery = useOutreachList(activeUserId, undefined)

  const needsReview = useMemo(() => {
    const combined = [...(pendingQuery.data ?? []), ...(editedQuery.data ?? [])]
    return combined.sort((a, b) => b.generated_at.localeCompare(a.generated_at))
  }, [pendingQuery.data, editedQuery.data])

  const historyItems = useMemo(
    () => (historyQuery.data ?? []).filter((item) => HISTORY_STATUSES.has(item.status)),
    [historyQuery.data],
  )

  const rows = tab === 'needs-review' ? needsReview : historyItems
  const selected = rows.find((item) => item.id === selectedId) ?? null

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

  function selectTab(next: QueueTab) {
    setTab(next)
    setSelectedId(null)
  }

  const columns: TableColumn<OutreachResponse>[] = [
    {
      key: 'message',
      header: 'Draft',
      render: (row) => truncate(row.final_message ?? row.draft_message),
    },
    { key: 'channel', header: 'Channel', render: (row) => getChannelLabel(row.channel) },
    { key: 'status', header: 'Status', render: (row) => <StatusBadge status={row.status} /> },
    {
      key: 'generated',
      header: 'Generated',
      render: (row) => formatDateTime(row.generated_at),
      hideOnMobile: true,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Outreach Queue"
        description="Every draft here was generated automatically. Nothing is sent without your approval."
      />

      <div className="mb-4 flex gap-2" role="tablist" aria-label="Outreach queue tabs">
        <Button
          type="button"
          variant={tab === 'needs-review' ? 'primary' : 'secondary'}
          role="tab"
          aria-selected={tab === 'needs-review'}
          onClick={() => selectTab('needs-review')}
        >
          Needs review{needsReview.length > 0 ? ` (${needsReview.length})` : ''}
        </Button>
        <Button
          type="button"
          variant={tab === 'history' ? 'primary' : 'secondary'}
          role="tab"
          aria-selected={tab === 'history'}
          onClick={() => selectTab('history')}
        >
          History
        </Button>
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isError ? (
        <ErrorState message="Couldn't load the outreach queue." onRetry={retry} />
      ) : rows.length === 0 ? (
        tab === 'needs-review' ? (
          <EmptyState
            icon={<Inbox className="h-8 w-8" />}
            title="Nothing waiting for your review"
            description="You're caught up — new outreach drafts will show up here as soon as they're generated."
          />
        ) : (
          <EmptyState
            title="No outreach history yet"
            description="Approved, rejected, and sent outreach will appear here."
          />
        )
      ) : (
        <div className="flex flex-col gap-4">
          <Table
            aria-label={tab === 'needs-review' ? 'Outreach needing review' : 'Outreach history'}
            columns={columns}
            rows={rows}
            getRowKey={(row) => row.id}
            onRowClick={(row) => setSelectedId(row.id === selectedId ? null : row.id)}
          />
          {selected ? (
            <OutreachReviewPanel key={selected.id} outreach={selected} userId={activeUserId} />
          ) : null}
        </div>
      )}
    </div>
  )
}
