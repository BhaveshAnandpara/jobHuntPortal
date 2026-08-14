/**
 * Shared render/wrapper helper for hook tests across `src/api/*.test.ts` —
 * a fresh `QueryClient` per test so cache state never leaks between tests
 * (mirrors `app/queryClient.ts`'s retry policy but disables retries
 * entirely so error-path tests resolve immediately instead of waiting out
 * backoff).
 *
 * Not exported outside `src/api/**` — this is test-only infrastructure, not
 * a shared app hook.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

export function createWrapper(queryClient: QueryClient = createTestQueryClient()) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}
