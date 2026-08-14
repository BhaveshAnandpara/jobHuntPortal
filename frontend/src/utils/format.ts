/**
 * Small, presentation-only formatting helpers shared across features. No
 * business logic — every value here is already computed by the backend
 * (see docs/frontend/architecture.md#thin-client-principle); this only
 * changes how it's displayed.
 */

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** `0.91` -> `"91%"` — every match/relevance score is a 0.0-1.0 float on
 * the wire (see shared/types/dto.py); this is the one place it's turned
 * into a percentage string. */
export function formatScorePercent(score: number): string {
  return `${Math.round(score * 100)}%`
}
