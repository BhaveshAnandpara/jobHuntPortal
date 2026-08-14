import { createRef } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Input } from './Input'

describe('Input', () => {
  it('forwards a ref to the underlying <input>', () => {
    const ref = createRef<HTMLInputElement>()
    render(<Input ref={ref} aria-label="Email" />)
    expect(ref.current).toBeInstanceOf(HTMLInputElement)
  })

  it('does not set aria-invalid by default', () => {
    render(<Input aria-label="Email" />)
    expect(screen.getByLabelText('Email')).not.toHaveAttribute('aria-invalid')
  })

  it('sets aria-invalid and an error border when invalid', () => {
    render(<Input aria-label="Email" invalid />)
    const input = screen.getByLabelText('Email')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input.className).toContain('border-red-400')
  })

  it('accepts typed input', async () => {
    render(<Input aria-label="Email" />)
    const input = screen.getByLabelText('Email')
    await userEvent.type(input, 'a@b.com')
    expect(input).toHaveValue('a@b.com')
  })
})
