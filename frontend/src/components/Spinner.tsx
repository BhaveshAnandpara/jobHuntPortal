/**
 * Owner: frontend-design-agent. Plain spinner only — no "AI thinking"
 * animation, per docs/frontend/design-system.md's explicit direction.
 * Consumers: every feature (loading states).
 */

import { Loader2 } from 'lucide-react'

type SpinnerProps = {
  className?: string
  /** Accessible label announced to screen readers while loading. */
  label?: string
}

export function Spinner({ className = 'h-5 w-5', label = 'Loading' }: SpinnerProps) {
  return (
    <span role="status" className="inline-flex">
      <Loader2 className={`animate-spin text-gray-400 ${className}`} aria-hidden />
      <span className="sr-only">{label}</span>
    </span>
  )
}
