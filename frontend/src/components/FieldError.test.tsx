import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FieldError } from './FieldError'

describe('FieldError', () => {
  it('renders nothing when no message is given', () => {
    const { container } = render(<FieldError />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the message as an alert when given', () => {
    render(<FieldError message="This field is required." />)
    expect(screen.getByRole('alert')).toHaveTextContent('This field is required.')
  })
})
