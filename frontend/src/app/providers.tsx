/**
 * Global provider composition — the only place these are mounted. Feature
 * agents consume `useCurrentUserId()` / `useQuery` / `toast` (from
 * `sonner`) without ever mounting a second instance of any provider here.
 *
 * Owner: frontend-shell-agent.
 */

import type { ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './queryClient'
import { IdentityProvider } from '../hooks/IdentityProvider'
import { Toaster } from '../components'

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        {children}
        <Toaster />
      </IdentityProvider>
    </QueryClientProvider>
  )
}
