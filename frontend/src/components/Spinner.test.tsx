import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Spinner } from './Spinner'

describe('Spinner', () => {
  it('exposes role="status" with a default accessible label', () => {
    render(<Spinner />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
  })

  it('accepts a custom accessible label', () => {
    render(<Spinner label="Analyzing job…" />)
    expect(screen.getByRole('status')).toHaveTextContent('Analyzing job…')
  })
})
