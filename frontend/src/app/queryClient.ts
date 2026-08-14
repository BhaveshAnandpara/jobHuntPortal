/**
 * The one `QueryClient` for the app. See
 * docs/frontend/state-management.md#server-state — every feature's server
 * state lives in this cache; nothing duplicates it into a separate store.
 *
 * Owner: frontend-shell-agent.
 */

import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'

function shouldRetry(failureCount: number, error: Error): boolean {
  // Never retry a request the server actively rejected (4xx) or a network
  // failure we already surfaced via the persistent banner (see
  // docs/frontend/error-handling.md) — only retry transient 5xx/unknown
  // failures, and only twice.
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false
  }
  return failureCount < 2
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetry,
    },
    mutations: {
      retry: false,
    },
  },
})
