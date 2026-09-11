/**
 * Owner: frontend-design-agent. Loading placeholder for content that will
 * shortly be replaced by real data (see the loading rows throughout
 * docs/frontend/routes.md). Consumers: every feature.
 *
 * T2 (docs/frontend/frontend-revamp-spec.md): internals are now shadcn's
 * `ui/skeleton`. The exported contract is unchanged (`className` only) and
 * the `aria-hidden` treatment below is kept — shadcn's skeleton does not set
 * it, and this app's convention is that a placeholder is never announced.
 */

import { Skeleton as ShadcnSkeleton } from './ui/skeleton'
import { cn } from '@/lib/utils'

export function Skeleton({ className = '' }: { className?: string }) {
  // Purely decorative placeholder — hidden from assistive tech so screen
  // readers don't announce an empty, meaningless block while content loads.
  return <ShadcnSkeleton aria-hidden="true" className={cn('bg-gray-200', className)} />
}
