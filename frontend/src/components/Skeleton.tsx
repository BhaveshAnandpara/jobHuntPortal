/**
 * Owner: frontend-design-agent. Loading placeholder for content that will
 * shortly be replaced by real data (see the loading rows throughout
 * docs/frontend/routes.md). Consumers: every feature.
 */

export function Skeleton({ className = '' }: { className?: string }) {
  // Purely decorative placeholder — hidden from assistive tech so screen
  // readers don't announce an empty, meaningless block while content loads.
  return <div aria-hidden="true" className={`animate-pulse rounded-md bg-gray-200 ${className}`} />
}
