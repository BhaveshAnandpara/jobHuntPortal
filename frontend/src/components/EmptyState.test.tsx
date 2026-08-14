import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('renders the required title', () => {
    render(<EmptyState title="No resumes yet" />)
    expect(screen.getByText('No resumes yet')).toBeInTheDocument()
  })

  it('renders an optional description', () => {
    render(<EmptyState title="No resumes yet" description="Upload one to get started." />)
    expect(screen.getByText('Upload one to get started.')).toBeInTheDocument()
  })

  it('omits the description paragraph when none is given', () => {
    render(<EmptyState title="No resumes yet" />)
    expect(screen.queryByText('Upload one to get started.')).not.toBeInTheDocument()
  })

  it('hides a decorative icon from assistive tech', () => {
    const { container } = render(<EmptyState title="No resumes yet" icon={<svg data-testid="icon" />} />)
    const iconWrapper = container.querySelector('[aria-hidden="true"]')
    expect(iconWrapper).toContainElement(screen.getByTestId('icon'))
  })

  it('renders an optional call-to-action', () => {
    render(<EmptyState title="No resumes yet" action={<button type="button">Upload</button>} />)
    expect(screen.getByRole('button', { name: 'Upload' })).toBeInTheDocument()
  })
})
