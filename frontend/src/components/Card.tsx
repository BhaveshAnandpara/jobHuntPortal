/**
 * Owner: frontend-design-agent. Input: `children`, optional `className`.
 * Output: a bordered content container. Consumers: every feature.
 */

import type { HTMLAttributes } from 'react'

export function Card({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-lg border border-gray-200 bg-white p-6 shadow-sm ${className}`}
      {...rest}
    />
  )
}
