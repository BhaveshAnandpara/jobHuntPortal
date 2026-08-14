/**
 * Owner: frontend-design-agent. The one tabular-data primitive — see
 * docs/frontend/design-system.md#responsive-behavior: a desktop table that
 * collapses to a stacked card list below the ~768px breakpoint, same data,
 * no columns dropped, just re-laid-out. This did not exist in the skeleton;
 * it is new work for this wave (see agent-ownership.md).
 *
 * Input: `columns` (a render function per column, not raw field access, so
 * a column can render a `StatusBadge`, a link, formatted currency, etc.),
 * `rows`, `getRowKey`, optional `onRowClick`.
 * Output: an accessible `<table>` on md+ viewports and a `<ul>` of cards
 * below it — both driven from the same `columns`/`rows` data, so there is
 * only one place a feature agent describes "what a row looks like."
 * Consumers: any feature list view (Opportunities, Outreach queue, etc.).
 *
 * This component never fetches data and renders nothing for an empty
 * `rows` array — pair it with `EmptyState` in the consuming feature for the
 * "no rows yet" case, the same convention every other list-shaped page in
 * this app follows.
 */

import type { KeyboardEvent, ReactNode } from 'react'

export type TableColumn<T> = {
  /** Unique key for this column — also used as the React key per cell. */
  key: string
  header: string
  render: (row: T) => ReactNode
  headerClassName?: string
  cellClassName?: string
  /**
   * Omit this column from the collapsed mobile card view — for a column
   * that's purely a desktop affordance (e.g. a decorative icon column) and
   * would be redundant once every other field is already stacked. Defaults
   * to shown on both.
   */
  hideOnMobile?: boolean
}

export type TableProps<T> = {
  columns: TableColumn<T>[]
  rows: T[]
  getRowKey: (row: T) => string
  /** Makes each row/card a keyboard- and click-activatable target. */
  onRowClick?: (row: T) => void
  'aria-label'?: string
}

function handleActivationKeyDown(event: KeyboardEvent<HTMLElement>, activate: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    activate()
  }
}

export function Table<T>({ columns, rows, getRowKey, onRowClick, ...rest }: TableProps<T>) {
  const clickable = Boolean(onRowClick)
  const visibleMobileColumns = columns.filter((column) => !column.hideOnMobile)

  return (
    <>
      {/* Desktop / tablet: a real table, hidden below the md breakpoint. */}
      <div className="hidden overflow-x-auto rounded-lg border border-gray-200 md:block">
        <table className="w-full border-collapse text-sm" aria-label={rest['aria-label']}>
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={`px-4 py-2.5 text-left text-xs font-medium text-gray-500 ${column.headerClassName ?? ''}`}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const key = getRowKey(row)
              const activate = () => onRowClick?.(row)
              return (
                <tr
                  key={key}
                  className={`border-b border-gray-100 last:border-0 ${
                    clickable ? 'cursor-pointer hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand' : ''
                  }`}
                  onClick={clickable ? activate : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  role={clickable ? 'button' : undefined}
                  onKeyDown={clickable ? (event) => handleActivationKeyDown(event, activate) : undefined}
                >
                  {columns.map((column) => (
                    <td key={column.key} className={`px-4 py-3 text-gray-900 ${column.cellClassName ?? ''}`}>
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile: card-collapse — same data, stacked layout, below md. */}
      <ul className="flex flex-col gap-3 md:hidden" aria-label={rest['aria-label']}>
        {rows.map((row) => {
          const key = getRowKey(row)
          const activate = () => onRowClick?.(row)
          return (
            <li key={key}>
              <div
                className={`flex flex-col gap-2 rounded-lg border border-gray-200 bg-white p-4 ${
                  clickable ? 'cursor-pointer hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand' : ''
                }`}
                onClick={clickable ? activate : undefined}
                tabIndex={clickable ? 0 : undefined}
                role={clickable ? 'button' : undefined}
                onKeyDown={clickable ? (event) => handleActivationKeyDown(event, activate) : undefined}
              >
                {visibleMobileColumns.map((column) => (
                  <div key={column.key} className="flex items-center justify-between gap-3">
                    <span className="text-xs font-medium text-gray-500">{column.header}</span>
                    <span className="text-sm text-gray-900">{column.render(row)}</span>
                  </div>
                ))}
              </div>
            </li>
          )
        })}
      </ul>
    </>
  )
}
