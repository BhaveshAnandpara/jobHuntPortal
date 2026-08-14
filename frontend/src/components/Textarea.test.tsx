import { createRef } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Textarea } from './Textarea'

describe('Textarea', () => {
  it('forwards a ref to the underlying <textarea>', () => {
    const ref = createRef<HTMLTextAreaElement>()
    render(<Textarea ref={ref} aria-label="Notes" />)
    expect(ref.current).toBeInstanceOf(HTMLTextAreaElement)
  })

  it('sets aria-invalid and an error border when invalid', () => {
    render(<Textarea aria-label="Notes" invalid />)
    const textarea = screen.getByLabelText('Notes')
    expect(textarea).toHaveAttribute('aria-invalid', 'true')
    expect(textarea.className).toContain('border-red-400')
  })
})
