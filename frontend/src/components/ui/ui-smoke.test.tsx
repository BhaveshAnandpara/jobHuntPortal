/**
 * T1 acceptance criterion (docs/frontend/frontend-revamp-spec.md): "CLI-added
 * components build and render in isolation."
 *
 * These are the raw shadcn primitives under src/components/ui — the files the
 * shadcn CLI generates and owns. The app never imports them directly; it goes
 * through the wrappers in src/components, which have their own behavioral
 * tests. So this file deliberately only smoke-tests that each generated
 * primitive mounts and renders its children, which is what would break if a
 * future `shadcn add`/registry change pulled in a dependency this project
 * does not have (the way the stock `sonner` file arrives importing
 * `next-themes`).
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Badge } from './badge'
import { Button } from './button'
import { Card, CardContent, CardHeader, CardTitle } from './card'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './dialog'
import { Input } from './input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select'
import { Skeleton } from './skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './table'
import { Textarea } from './textarea'
import { Toaster } from './sonner'

describe('shadcn primitives (src/components/ui)', () => {
  it('button renders as a <button> with its cva variant data attributes', () => {
    render(<Button variant="outline">Click</Button>)
    const button = screen.getByRole('button', { name: 'Click' })
    expect(button.tagName).toBe('BUTTON')
    expect(button).toHaveAttribute('data-slot', 'button')
    expect(button).toHaveAttribute('data-variant', 'outline')
  })

  it('badge renders as a <span>', () => {
    const { container } = render(<Badge>Live</Badge>)
    const badge = container.querySelector('[data-slot="badge"]')
    expect(badge?.tagName).toBe('SPAN')
    expect(badge).toHaveTextContent('Live')
  })

  it('input and textarea render their native elements', () => {
    render(
      <>
        <Input aria-label="Email" />
        <Textarea aria-label="Notes" />
      </>,
    )
    expect(screen.getByLabelText('Email').tagName).toBe('INPUT')
    expect(screen.getByLabelText('Notes').tagName).toBe('TEXTAREA')
  })

  it('skeleton renders a placeholder element', () => {
    const { container } = render(<Skeleton className="h-4 w-32" />)
    expect(container.querySelector('[data-slot="skeleton"]')).toBeInTheDocument()
  })

  it('card renders its header/content sub-parts', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Pipeline</CardTitle>
        </CardHeader>
        <CardContent>12 opportunities</CardContent>
      </Card>,
    )
    expect(screen.getByText('Pipeline')).toBeInTheDocument()
    expect(screen.getByText('12 opportunities')).toBeInTheDocument()
  })

  it('table renders accessible table semantics', () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Company</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Acme Corp</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Company' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Acme Corp' })).toBeInTheDocument()
  })

  it('dialog renders content when open', () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Delete resume</DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogContent>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: 'Delete resume' })).toBeInTheDocument()
  })

  it('select renders a combobox trigger showing the selected value', () => {
    render(
      <Select value="a" onValueChange={() => {}}>
        <SelectTrigger aria-label="Status">
          <SelectValue placeholder="Choose" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">Option A</SelectItem>
        </SelectContent>
      </Select>,
    )
    expect(screen.getByRole('combobox', { name: 'Status' })).toBeInTheDocument()
    expect(screen.getByText('Option A')).toBeInTheDocument()
  })

  it('sonner toaster mounts without a theme-provider dependency', () => {
    // Regression guard for the local edit to sonner.tsx: the stock registry
    // file calls `useTheme()` from `next-themes`, which this project does not
    // depend on, so it would throw on render.
    const { container } = render(<Toaster />)
    expect(container.querySelector('section[aria-live="polite"]')).toBeInTheDocument()
  })
})
