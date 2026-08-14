import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { toast } from 'sonner'
import { Toaster } from './Toaster'

describe('Toaster', () => {
  it('mounts an aria-live region so toast announcements reach screen readers', () => {
    const { container } = render(<Toaster />)
    // sonner always mounts an `aria-live="polite"` section (the toast list
    // itself only appears once a toast exists) — this proves the component
    // actually wires up sonner rather than rendering an empty shell.
    expect(container.querySelector('section[aria-live="polite"]')).toBeInTheDocument()
  })

  it('renders a toast when a feature calls toast.success/toast.error (the documented usage pattern)', async () => {
    render(<Toaster />)
    toast.success('Saved')
    expect(await screen.findByText('Saved')).toBeInTheDocument()
    toast.error('Failed')
    expect(await screen.findByText('Failed')).toBeInTheDocument()
  })
})
