/**
 * The one badge component for every backend lifecycle enum this app
 * renders — see docs/frontend/design-system.md#status-badges and
 * src/utils/status.ts for the enum -> category mapping this consumes.
 *
 * Owner: frontend-design-agent.
 * Input: `status` — any backend status enum value (typed via
 *        src/api/types.ts's aliases).
 * Output: a colored, labeled badge. No feature re-implements this styling.
 * Consumers: every feature that renders a lifecycle status.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/badge`, with the five status *categories* expressed as a `cva` variant
 * set layered over it. The enum -> category mapping itself is untouched and
 * still lives in src/utils/status.ts — this file only decides what a category
 * looks like.
 */

import { cva } from 'class-variance-authority'
import { Badge } from './ui/badge'
import { cn } from '@/lib/utils'
import { getStatusPresentation, type StatusCategory } from '../utils/status'
import type {
  ApplicationStatus,
  ContactStatus,
  JobProcessingStatus,
  MatchRecommendation,
  OutreachStatus,
  ResumeStatus,
} from '../api/types'

type KnownStatus =
  | ApplicationStatus
  | OutreachStatus
  | ResumeStatus
  | ContactStatus
  | JobProcessingStatus
  | MatchRecommendation

/**
 * Category -> color pairing. Each pair is one of the WCAG-AA-verified
 * foreground/background token pairs declared in src/index.css's `@theme`
 * block; do not inline a raw color here.
 */
const statusBadgeVariants = cva('rounded-full border-transparent px-2.5 py-0.5 text-xs font-medium', {
  variants: {
    category: {
      progress: 'bg-status-progress-bg text-status-progress',
      attention: 'bg-status-attention-bg text-status-attention',
      positive: 'bg-status-positive-bg text-status-positive',
      negative: 'bg-status-negative-bg text-status-negative',
      neutral: 'bg-status-neutral-bg text-status-neutral',
    } satisfies Record<StatusCategory, string>,
  },
})

export function StatusBadge({ status }: { status: KnownStatus }) {
  const { label, category } = getStatusPresentation(status)
  return (
    <Badge variant="secondary" className={cn('h-auto gap-1.5', statusBadgeVariants({ category }))}>
      {category === 'progress' ? (
        // Design direction: "In progress" gets a subtle pulse, never a
        // spinner (see docs/frontend/design-system.md#status-badges and the
        // "avoid... animated thinking indicators beyond a plain spinner"
        // rule). A pulsing dot, not the label itself, carries the motion so
        // the text stays readable.
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-current" aria-hidden />
      ) : null}
      {label}
    </Badge>
  )
}
