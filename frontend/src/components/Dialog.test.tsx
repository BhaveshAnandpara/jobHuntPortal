import { useRef, useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Dialog } from './Dialog'

describe('Dialog', () => {
  it('renders nothing when closed', () => {
    render(
      <Dialog open={false} onOpenChange={() => {}} title="Delete resume">
        <p>Body</p>
      </Dialog>,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders the title and children when open', () => {
    render(
      <Dialog open onOpenChange={() => {}} title="Delete resume">
        <p>Are you sure?</p>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: 'Delete resume' })).toBeInTheDocument()
    expect(screen.getByText('Are you sure?')).toBeInTheDocument()
  })

  it('falls back to a screen-reader-only description when none is given (Radix a11y requirement)', () => {
    render(
      <Dialog open onOpenChange={() => {}} title="Delete resume">
        <p>Are you sure?</p>
      </Dialog>,
    )
    // Falls back to the title text, visually hidden — Radix requires a
    // Description or aria-describedby, but no visible copy is warranted
    // when the body is self-explanatory.
    const description = screen.getAllByText('Delete resume').find((el) => el.className.includes('sr-only'))
    expect(description).toBeDefined()
  })

  it('renders a visible description when one is given', () => {
    render(
      <Dialog open onOpenChange={() => {}} title="Delete resume" description="This cannot be undone.">
        <p>Body</p>
      </Dialog>,
    )
    const description = screen.getByText('This cannot be undone.')
    expect(description.className).not.toContain('sr-only')
  })

  it('has an accessibly labeled close button that closes the dialog', async () => {
    const onOpenChange = vi.fn()
    render(
      <Dialog open onOpenChange={onOpenChange} title="Delete resume">
        <p>Body</p>
      </Dialog>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('Step 12 regression: with a `triggerRef`, closing via Escape returns focus to that element — controlled dialogs (no Dialog.Trigger) otherwise leave focus on document.body', async () => {
    function Harness() {
      const [open, setOpen] = useState(false)
      const triggerRef = useRef<HTMLButtonElement | null>(null)
      return (
        <>
          <button ref={triggerRef} onClick={() => setOpen(true)}>
            Delete
          </button>
          <Dialog open={open} onOpenChange={setOpen} title="Delete resume" triggerRef={triggerRef}>
            <p>Are you sure?</p>
          </Dialog>
        </>
      )
    }
    render(<Harness />)

    const trigger = screen.getByRole('button', { name: 'Delete' })
    await userEvent.click(trigger)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('without a `triggerRef`, closing behaves exactly as before (no crash, no forced focus target)', async () => {
    const onOpenChange = vi.fn()
    render(
      <Dialog open onOpenChange={onOpenChange} title="Delete resume">
        <p>Body</p>
      </Dialog>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
