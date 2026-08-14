import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ErrorState } from './ErrorState'

describe('ErrorState', () => {
  it('renders the message as an alert for assistive tech', () => {
    render(<ErrorState message="Could not load applications." />)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load applications.')
  })

  it('omits the retry button when onRetry is not provided', () => {
    render(<ErrorState message="Could not load applications." />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('calls onRetry when the retry button is clicked', async () => {
    const onRetry = vi.fn()
    render(<ErrorState message="Could not load applications." onRetry={onRetry} />)
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
