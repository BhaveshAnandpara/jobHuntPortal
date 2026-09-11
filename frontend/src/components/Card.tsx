/**
 * Owner: frontend-design-agent. Input: `children`, optional `className`.
 * Output: a bordered content container. Consumers: every feature.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/card`. The exported contract is unchanged — a single element that takes
 * arbitrary `HTMLAttributes<HTMLDivElement>` and renders `children` directly,
 * with no required Header/Content/Footer sub-parts, because every existing
 * consumer composes its own heading markup inside `<Card>`.
 *
 * shadcn's card defaults are deliberately overridden back to this app's
 * established container treatment (block flow, `rounded-lg`, a visible
 * 1px border, `p-6`, no clipping). Adopting shadcn's flex/`rounded-xl`/
 * `overflow-hidden` card wholesale would silently re-flow and clip panels on
 * every page; restyling those is the later per-page tickets' job (T5–T10).
 */

import type { HTMLAttributes } from 'react'
import { Card as ShadcnCard } from './ui/card'
import { cn } from '@/lib/utils'

export function Card({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <ShadcnCard
      className={cn(
        'block overflow-visible rounded-lg border border-gray-200 bg-white p-6 shadow-sm ring-0',
        className,
      )}
      {...rest}
    />
  )
}
