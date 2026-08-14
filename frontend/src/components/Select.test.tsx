import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Select } from './Select'

const OPTIONS = [
  { value: 'a', label: 'Option A' },
  { value: 'b', label: 'Option B' },
]

describe('Select', () => {
  it('renders the placeholder when no value is selected', () => {
    render(<Select options={OPTIONS} onValueChange={() => {}} placeholder="Choose one" />)
    expect(screen.getByText('Choose one')).toBeInTheDocument()
  })

  it('renders the selected option label', () => {
    render(<Select options={OPTIONS} value="b" onValueChange={() => {}} />)
    expect(screen.getByText('Option B')).toBeInTheDocument()
  })

  it('opens the listbox and calls onValueChange when an option is chosen', async () => {
    const onValueChange = vi.fn()
    render(<Select options={OPTIONS} onValueChange={onValueChange} placeholder="Choose one" />)

    await userEvent.click(screen.getByRole('combobox'))
    const option = await screen.findByRole('option', { name: 'Option A' })
    await userEvent.click(option);

    expect(onValueChange).toHaveBeenCalledWith('a')
  })

  it('disables the trigger when disabled', () => {
    render(<Select options={OPTIONS} onValueChange={() => {}} disabled />)
    expect(screen.getByRole('combobox')).toHaveAttribute('data-disabled')
  })

  it('marks the trigger invalid via aria-invalid', () => {
    render(<Select options={OPTIONS} onValueChange={() => {}} invalid />)
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true')
  })
})
