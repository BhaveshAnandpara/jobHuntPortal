/**
 * Resume/Profile Service (profiles) — see
 * docs/frontend/api-mapping.md#resumeprofile-service. Owner:
 * frontend-api-agent.
 */

import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { ResumeProfile } from './types'

export function listProfiles(userId: string): Promise<ResumeProfile[]> {
  return apiClient.get<ResumeProfile[]>(`/profiles?user_id=${userId}`)
}

export function getProfile(profileId: string): Promise<ResumeProfile> {
  return apiClient.get<ResumeProfile>(`/profiles/${profileId}`)
}

/** Fetch-once + `refetchOnWindowFocus` — reacts to resume upload/delete invalidation, not polling. */
export function useProfiles(userId: string) {
  return useQuery({
    queryKey: queryKeys.profiles(userId),
    queryFn: () => listProfiles(userId),
    enabled: Boolean(userId),
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
