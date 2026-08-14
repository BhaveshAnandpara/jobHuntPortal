/**
 * Job Matching Service — see docs/frontend/api-mapping.md#job-matching-service.
 * Owner: frontend-api-agent.
 */

import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { JobMatchResponse } from './types'

export function getJobMatch(jobId: string): Promise<JobMatchResponse> {
  return apiClient.get<JobMatchResponse>(`/jobs/${jobId}/matches`)
}

/**
 * Fetch-once + `refetchOnWindowFocus` — a one-time computation, not an
 * in-progress process to poll. Per api-mapping.md, only used for
 * `recommendation`/`job_match_id`; score/matched/missing skills should be
 * read from the already-fetched `ApplicationResponse` instead.
 */
export function useJobMatch(jobId: string) {
  return useQuery({
    queryKey: queryKeys.match(jobId),
    queryFn: () => getJobMatch(jobId),
    enabled: Boolean(jobId),
  })
}
