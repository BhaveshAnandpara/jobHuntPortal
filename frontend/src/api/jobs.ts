/**
 * Job Ingestion Service — see docs/frontend/api-mapping.md#job-ingestion-service.
 * Owner: frontend-api-agent.
 *
 * Job Discovery Service's `/job-sources` is intentionally not wired here —
 * see api-mapping.md#job-discovery-service (FUTURE ENHANCEMENT, not part
 * of this MVP frontend's page list).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { IngestJobUrlRequest, JobResponse } from './types'

export function ingestJobUrl(body: IngestJobUrlRequest): Promise<JobResponse> {
  return apiClient.post<JobResponse>('/jobs/ingest-url', body)
}

export function getJob(jobId: string): Promise<JobResponse> {
  return apiClient.get<JobResponse>(`/jobs/${jobId}`)
}

/** Fetch-once + `refetchOnWindowFocus` — used by Opportunity Detail's Job Information panel. */
export function useJob(jobId: string) {
  return useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: () => getJob(jobId),
    enabled: Boolean(jobId),
  })
}

/**
 * Invalidates `queryKeys.applications(userId)` on success per
 * api-mapping.md — note this alone will not immediately show the new row,
 * since the `Application` row appears asynchronously after this call
 * returns (see docs/frontend/user-flows.md#job-submission-flow); that's
 * expected, not a bug, and not something to paper over with a client-side
 * poll started from inside this mutation (a page-specific UX decision left
 * to the consuming feature agent).
 */
export function useIngestJobUrl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: IngestJobUrlRequest) => ingestJobUrl(body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.applications(variables.user_id) })
    },
  })
}
