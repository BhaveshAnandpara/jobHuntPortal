/**
 * Contact Discovery Service — see
 * docs/frontend/api-mapping.md#contact-discovery-service. Owner:
 * frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { ContactResponse, TriggerContactSearchRequest, TriggerContactSearchResponse } from './types'

export function listContacts(jobId: string): Promise<ContactResponse[]> {
  return apiClient.get<ContactResponse[]>(`/jobs/${jobId}/contacts`)
}

/**
 * Manual re-trigger. The caller supplies `company`/`title`/`location`
 * itself (from already-fetched job/application data) — Contact Discovery
 * Service cannot look these up; see api-mapping.md's note on this
 * endpoint's resolved gap.
 */
export function triggerContactSearch(
  jobId: string,
  body: TriggerContactSearchRequest,
): Promise<TriggerContactSearchResponse> {
  return apiClient.post<TriggerContactSearchResponse>(`/jobs/${jobId}/contacts/search`, body)
}

/**
 * Fetch-once + `refetchOnWindowFocus` — always 200 (empty list is a valid
 * "no contacts yet" result, not an error state); no polling interval here
 * per async-workflows.md (the parent Opportunity Detail page re-renders
 * this panel by polling `GET /applications/{id}` instead, per
 * async-workflows.md#full-pipeline-progression-on-opportunity-detail-no-page-reload).
 */
export function useContacts(jobId: string) {
  return useQuery({
    queryKey: queryKeys.contacts(jobId),
    queryFn: () => listContacts(jobId),
    enabled: Boolean(jobId),
  })
}

/**
 * Invalidates `queryKeys.contacts(jobId)` on success. api-mapping.md notes
 * this should happen "after a delay" since the real result arrives async
 * via `contacts.found` — deciding when (or whether) to poll afterward for
 * the result to actually appear is a page-specific UX decision (e.g.
 * pairing this with a temporary `pollUntil`/`pollAlways` on `useContacts`)
 * left to the consuming feature agent, not hard-coded here as a `setTimeout`.
 */
export function useTriggerContactSearch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      jobId,
      body,
    }: {
      jobId: string
      body: TriggerContactSearchRequest
    }) => triggerContactSearch(jobId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contacts(variables.jobId) })
    },
  })
}
