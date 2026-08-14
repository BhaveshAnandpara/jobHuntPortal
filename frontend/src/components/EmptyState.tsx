/**
 * Owner: frontend-design-agent. The single component reused for every
 * empty state documented per-page in docs/frontend/routes.md — no feature
 * writes its own "nothing here yet" markup.
 *
 * Input: `icon`, `title`, `description`, optional `action`.
 * Output: centered empty-state layout.
 * Consumers: every feature.
 */

import type { ReactNode } from 'react'

type EmptyStateProps = {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-gray-300 px-6 py-12 text-center">
      {icon ? (
        <div className="text-gray-400" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium text-gray-900">{title}</p>
      {description ? <p className="text-sm text-gray-500">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}
