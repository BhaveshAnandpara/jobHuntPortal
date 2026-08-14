/**
 * TanStack Query `refetchInterval` conventions per
 * docs/frontend/async-workflows.md — polling only, no WebSockets/SSE this
 * step (and none planned until a real architecture decision says
 * otherwise). Feature agents pass one of these into a `useQuery`'s
 * `refetchInterval` option; none should hand-roll their own
 * `setInterval`.
 *
 * Owner: shared (see docs/frontend/repository-structure.md's ownership
 * table) — the first agent to need a polling helper adds it here rather
 * than duplicating one in a feature folder; subsequent agents extend this
 * file instead of writing a second version.
 */

import type { Query } from '@tanstack/react-query'

/**
 * Shape of a `useQuery`'s `refetchInterval` option. Query hooks that are
 * genuinely page-specific about *whether* they poll (per
 * docs/frontend/async-workflows.md's table — e.g. `/opportunities/:id`'s
 * interval depends on that one application's status, not just "is this
 * hook always polled") accept this as an optional parameter instead of
 * hard-coding a single interval that wouldn't fit every use site.
 */
export type RefetchInterval<TData> =
  | number
  | false
  | ((query: Query<TData, Error>) => number | false)

/**
 * Poll at a fixed interval until `isDone(data)` returns `true`, then stop.
 * Matches the per-page interval/stop-condition table in
 * docs/frontend/async-workflows.md (e.g. resume parsing: poll every 2s
 * until every resume's status is terminal).
 */
export function pollUntil<TData>(
  intervalMs: number,
  isDone: (data: TData | undefined) => boolean,
) {
  return (query: Query<TData, Error>): number | false => {
    return isDone(query.state.data) ? false : intervalMs
  }
}

/**
 * Poll at a fixed interval indefinitely (e.g. the Opportunities list, the
 * Outreach queue — see async-workflows.md's "never fully stops" rows).
 * `refetchIntervalInBackground: false` is TanStack Query's default, so a
 * backgrounded tab does not keep polling — no extra option needed here.
 */
export function pollAlways(intervalMs: number): number {
  return intervalMs
}
