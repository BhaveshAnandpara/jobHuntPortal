/**
 * Covers the `cn()` helper added in T1 (docs/frontend/frontend-revamp-spec.md).
 * The behavior that matters to every migrated primitive in T2 is the last
 * one: a caller-supplied `className` must *win* over the component's own base
 * classes rather than merely coexisting with them, because the shadcn
 * wrappers in src/components pass their overrides through this function.
 */

import { describe, expect, it } from 'vitest'
import { cn } from './utils'

describe('cn', () => {
  it('joins plain class strings', () => {
    expect(cn('px-2', 'py-1')).toBe('px-2 py-1')
  })

  it('drops falsy and conditional entries (clsx behavior)', () => {
    expect(cn('px-2', false, null, undefined, '', 'py-1')).toBe('px-2 py-1')
    expect(cn('px-2', { 'text-red-600': true, 'text-gray-900': false })).toBe('px-2 text-red-600')
  })

  it('flattens arrays', () => {
    expect(cn(['px-2', ['py-1']])).toBe('px-2 py-1')
  })

  it('lets a later conflicting Tailwind utility replace an earlier one (tailwind-merge behavior)', () => {
    // This is why the T2 wrappers can hand shadcn a `className` override and
    // trust it to take effect — e.g. Card's `rounded-lg` over shadcn's
    // `rounded-xl`, or Input's `h-auto` over shadcn's `h-8`.
    expect(cn('rounded-xl', 'rounded-lg')).toBe('rounded-lg')
    expect(cn('h-8 px-2.5', 'h-auto px-3')).toBe('h-auto px-3')
  })

  it('keeps non-conflicting utilities from both sides', () => {
    expect(cn('animate-pulse rounded-md bg-muted', 'bg-gray-200 h-4 w-32')).toBe(
      'animate-pulse rounded-md bg-gray-200 h-4 w-32',
    )
  })
})
