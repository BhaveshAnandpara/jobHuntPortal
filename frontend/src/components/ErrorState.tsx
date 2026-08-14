/**
 * Owner: frontend-design-agent. The single component reused for every
 * page-level error state in docs/frontend/routes.md and the
 * page-level/panel-scoped rows in docs/frontend/error-handling.md.
 *
 * Input: `message`, optional `onRetry`.
 * Output: message + retry action.
 * Consumers: every feature.
 */

import { AlertTriangle } from 'lucide-react'
import { Button } from './Button'

type ErrorStateProps = {
  message: string
  onRetry?: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-status-negative-bg px-6 py-12 text-center"
    >
      <AlertTriangle className="h-6 w-6 text-status-negative" aria-hidden />
      <p className="text-sm font-medium text-gray-900">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}
