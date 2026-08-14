import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Card } from './Card'

describe('Card', () => {
  it('renders children inside a bordered container', () => {
    render(<Card>Content</Card>)
    expect(screen.getByText('Content')).toBeInTheDocument()
  })

  it('merges a custom className with the base styles', () => {
    render(<Card className="custom-class">Content</Card>)
    const el = screen.getByText('Content')
    expect(el.className).toContain('custom-class')
    expect(el.className).toContain('rounded-lg')
    expect(el.className).toContain('border')
  })
})
