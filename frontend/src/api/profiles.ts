/**
 * Resume/Profile Service (profiles) — see
 * docs/frontend/api-mapping.md#resumeprofile-service. Owner:
 * frontend-api-agent.
 */

import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { ResumeProfile } from './types'

export function listProfiles(): Promise<ResumeProfile[]> {
  return apiClient.get<ResumeProfile[]>('/profiles')
}

export function getProfile(profileId: string): Promise<ResumeProfile> {
  return apiClient.get<ResumeProfile>(`/profiles/${profileId}`)
}

/** Fetch-once + `refetchOnWindowFocus` — reacts to resume upload/delete invalidation, not polling. */
export function useProfiles() {
  return useQuery({
    queryKey: queryKeys.profiles(),
    queryFn: () => listProfiles(),
  })
}

/** Used by Opportunity Detail's Selected Resume panel — fetch-once, no polling. */
export function useProfile(profileId: string) {
  return useQuery({
    queryKey: queryKeys.profile(profileId),
    queryFn: () => getProfile(profileId),
    enabled: Boolean(profileId),
  })
}
