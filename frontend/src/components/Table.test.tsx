import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Table, type TableColumn } from './Table'

type Row = { id: string; company: string; title: string; score: number }

const ROWS: Row[] = [
  { id: '1', company: 'Acme Corp', title: 'Backend Engineer', score: 92 },
  { id: '2', company: 'Globex', title: 'Frontend Engineer', score: 74 },
]

const COLUMNS: TableColumn<Row>[] = [
  { key: 'company', header: 'Company', render: (row) => row.company },
  { key: 'title', header: 'Title', render: (row) => row.title },
  { key: 'score', header: 'Score', render: (row) => `${row.score}%` },
]

describe('Table', () => {
  it('renders column headers', () => {
    render(<Table columns={COLUMNS} rows={ROWS} getRowKey={(row) => row.id} />)
    expect(screen.getByRole('columnheader', { name: 'Company' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Title' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Score' })).toBeInTheDocument()
  })

  it('renders every row via each column’s render function, in both the desktop table and the mobile card list', () => {
    render(<Table columns={COLUMNS} rows={ROWS} getRowKey={(row) => row.id} />)
    // Same data appears twice in the DOM (table + card list); the mobile
    // list is only hidden via CSS (`md:hidden`) so both render in jsdom,
    // which has no real viewport-driven `display` computation.
    expect(screen.getAllByText('Acme Corp')).toHaveLength(2)
    expect(screen.getAllByText('92%')).toHaveLength(2)
  })

  it('renders no interactive row semantics when onRowClick is not provided', () => {
    render(<Table columns={COLUMNS} rows={ROWS} getRowKey={(row) => row.id} />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('calls onRowClick when a desktop row is clicked', async () => {
    const onRowClick = vi.fn()
    render(<Table columns={COLUMNS} rows={ROWS} getRowKey={(row) => row.id} onRowClick={onRowClick} />)
    const [firstRow] = screen.getAllByRole('button', { name: /Acme Corp/i })
    await userEvent.click(firstRow)
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0])
  })

  it('activates a row via keyboard (Enter) for accessibility', async () => {
    const onRowClick = vi.fn()
    render(<Table columns={COLUMNS} rows={ROWS} getRowKey={(row) => row.id} onRowClick={onRowClick} />)
    const [firstRow] = screen.getAllByRole('button', { name: /Acme Corp/i })
    firstRow.focus()
    await userEvent.keyboard('{Enter}')
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0])
  })

  it('omits a column from the mobile card view when hideOnMobile is set', () => {
    const columnsWithMobileHidden: TableColumn<Row>[] = [
      ...COLUMNS,
      { key: 'decor', header: 'Icon', render: () => 'X', hideOnMobile: true },
    ]
    // A single row keeps the expected counts unambiguous: each mobile card
    // repeats every visible column's header label once per row, so with N
    // rows a normal header appears "1 (desktop <th>) + N (mobile cards)"
    // times — using one row makes that 1 + 1 = 2.
    render(<Table columns={columnsWithMobileHidden} rows={[ROWS[0]]} getRowKey={(row) => row.id} />)
    // "Icon" is hideOnMobile, so it only ever appears in the desktop table.
    expect(screen.getAllByText('Icon')).toHaveLength(1)
    expect(screen.getAllByText('Company')).toHaveLength(2)
  })

  it('renders nothing for an empty rows array (pairing with EmptyState is the consumer’s job)', () => {
    const { container } = render(<Table columns={COLUMNS} rows={[]} getRowKey={(row) => row.id} />)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(0)
    expect(container.querySelectorAll('li')).toHaveLength(0)
  })
})
