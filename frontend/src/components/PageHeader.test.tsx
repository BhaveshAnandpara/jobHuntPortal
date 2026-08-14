import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageHeader } from './PageHeader'

describe('PageHeader', () => {
  it('renders the title as the page heading', () => {
    render(<PageHeader title="Opportunities" />)
    expect(screen.getByRole('heading', { name: 'Opportunities' })).toBeInTheDocument()
  })

  it('renders an optional description', () => {
    render(<PageHeader title="Opportunities" description="Every job in your pipeline." />)
    expect(screen.getByText('Every job in your pipeline.')).toBeInTheDocument()
  })

  it('renders an optional primary action', () => {
    render(<PageHeader title="Dashboard" action={<button type="button">Analyze Job</button>} />)
    expect(screen.getByRole('button', { name: 'Analyze Job' })).toBeInTheDocument()
  })
})
