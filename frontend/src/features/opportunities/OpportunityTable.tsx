/**
 * The `/opportunities` pipeline table — the column definitions for the rows
 * returned by the page's single `GET /applications` fetch. Extracted from
 * `OpportunitiesPage.tsx` in T7 (docs/frontend/frontend-revamp-spec.md).
 *
 * Responsive behavior is **not** implemented here. The shared `Table`
 * primitive (src/components/Table.tsx) already renders these same
 * `columns`/`rows` twice — a real `<table>` at `md`+ and a stacked card list
 * below it, CSS-hidden either way — which is this app's documented
 * table→card collapse at ~768px (design-system.md#responsive-behavior, and
 * T7's "mobile viewport shows cards, not a squeezed table" criterion). This
 * file just describes what one row contains, once.
 *
 * Every column is a field `ApplicationResponse` already carries (see
 * routes.md#opportunities--opportunities): title, company, status,
 * match_score, updated_at. No derived "priority", "days in stage" or other
 * invented metric — the API returns a flat list of current rows and nothing
 * else (spec Section 3, item 1).
 *
 * Owner: frontend-opportunities-agent.
 */

import { StatusBadge, Table, type TableColumn } from '../../components'
import { formatDate, formatScorePercent } from '../../utils/format'
import type { ApplicationResponse } from '../../api/types'

const COLUMNS: TableColumn<ApplicationResponse>[] = [
  {
    key: 'opportunity',
    header: 'Opportunity',
    render: (row) => (
      <div className="min-w-0">
        <p className="truncate font-medium text-gray-900">{row.title}</p>
        <p className="truncate text-xs text-gray-500">{row.company}</p>
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    render: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'match_score',
    header: 'Match',
    headerClassName: 'md:text-right',
    cellClassName: 'md:text-right',
    render: (row) =>
      // `match_score` is null until the matching workflow has run — an
      // ordinary early-lifecycle state, not missing data, so it says so
      // rather than rendering a bare dash.
      row.match_score != null ? (
        <span className="font-medium tabular-nums text-gray-900">
          {formatScorePercent(row.match_score)}
        </span>
      ) : (
        <span className="text-gray-400">Not scored yet</span>
      ),
  },
  {
    key: 'updated_at',
    header: 'Last activity',
    render: (row) => <span className="text-gray-500">{formatDate(row.updated_at)}</span>,
  },
]

type OpportunityTableProps = {
  applications: ApplicationResponse[]
  onOpen: (application: ApplicationResponse) => void
}

export function OpportunityTable({ applications, onOpen }: OpportunityTableProps) {
  return (
    <Table
      aria-label="Opportunities"
      columns={COLUMNS}
      rows={applications}
      getRowKey={(row) => row.id}
      onRowClick={onOpen}
    />
  )
}
