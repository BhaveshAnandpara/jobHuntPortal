/**
 * Owner: frontend-design-agent. The one page-title/primary-action header
 * pattern — see design-system.md's "obvious primary actions" direction.
 *
 * Input: `title`, optional `description`, optional `action` (the page's
 * one primary-styled Button, if it has one).
 * Output: page header layout.
 * Consumers: every page-level feature component.
 */

import type { ReactNode } from 'react'

type PageHeaderProps = {
  title: string
  description?: string
  action?: ReactNode
}

export function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 pb-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">{title}</h1>
        {description ? <p className="mt-1 text-sm text-gray-500">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  )
}
