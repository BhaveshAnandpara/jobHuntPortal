/**
 * The `/opportunities` status filter control — T7
 * (docs/frontend/frontend-revamp-spec.md). Covers the behavior that isn't
 * visible in the page-level test: the WAI-ARIA tabs keyboard contract
 * (roving tabIndex, arrow/Home/End navigation with activation following
 * focus) and that the tab set comes from `pipeline.ts` rather than being
 * re-declared here.
 */

import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { STATUS_TABS, type StatusTab } from './pipeline'
import { StatusFilterTabs, type StatusTabCounts } from './StatusFilterTabs'

const COUNTS: StatusTabCounts = { active: 2, applied: 1, closed: 0, all: 3 }

function ControlledTabs({ onChange = () => {} }: { onChange?: (tab: StatusTab) => void }) {
  const [value, setValue] = useState<StatusTab>('active')
  return (
    <StatusFilterTabs
      value={value}
      onChange={(tab) => {
        setValue(tab)
        onChange(tab)
      }}
      counts={COUNTS}
      panelId="opportunities-list"
    />
  )
}

describe('StatusFilterTabs', () => {
  it('renders one tab per documented status group with its row count', () => {
    render(<ControlledTabs />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(STATUS_TABS.length)
    expect(screen.getByRole('tab', { name: 'Active 2' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Applied 1' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Closed 0' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'All 3' })).toBeInTheDocument()
  })

  it('points every tab at the region it filters', () => {
    render(<ControlledTabs />)

    for (const tab of screen.getAllByRole('tab')) {
      expect(tab).toHaveAttribute('aria-controls', 'opportunities-list')
    }
  })

  it('keeps only the selected tab in the tab order (roving tabIndex)', () => {
    render(<ControlledTabs />)

    expect(screen.getByRole('tab', { name: 'Active 2' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('tab', { name: 'Applied 1' })).toHaveAttribute('tabindex', '-1')
  })

  it('moves between tabs with the arrow keys, selecting as focus moves', async () => {
    const onChange = vi.fn()
    render(<ControlledTabs onChange={onChange} />)
    const user = userEvent.setup()

    await user.tab()
    expect(screen.getByRole('tab', { name: 'Active 2' })).toHaveFocus()

    await user.keyboard('{ArrowRight}')
    expect(onChange).toHaveBeenLastCalledWith('applied')
    expect(screen.getByRole('tab', { name: 'Applied 1' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Applied 1' })).toHaveAttribute('aria-selected', 'true')

    await user.keyboard('{ArrowLeft}')
    expect(onChange).toHaveBeenLastCalledWith('active')
    expect(screen.getByRole('tab', { name: 'Active 2' })).toHaveFocus()

    // Wraps around rather than dead-ending at the first tab.
    await user.keyboard('{ArrowLeft}')
    expect(onChange).toHaveBeenLastCalledWith('all')
    expect(screen.getByRole('tab', { name: 'All 3' })).toHaveFocus()
  })

  it('jumps to the first and last tab with Home and End', async () => {
    const onChange = vi.fn()
    render(<ControlledTabs onChange={onChange} />)
    const user = userEvent.setup()

    await user.tab()
    await user.keyboard('{End}')
    expect(onChange).toHaveBeenLastCalledWith('all')
    expect(screen.getByRole('tab', { name: 'All 3' })).toHaveFocus()

    await user.keyboard('{Home}')
    expect(onChange).toHaveBeenLastCalledWith('active')
    expect(screen.getByRole('tab', { name: 'Active 2' })).toHaveFocus()
  })
})
