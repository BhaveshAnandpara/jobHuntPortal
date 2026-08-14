/**
 * Tracking Service — see docs/frontend/api-mapping.md#tracking-service.
 * Owner: frontend-api-agent.
 *
 * This is the one list surface for "things I'm pursuing" — there is no
 * separate `getJobs` list function; see
 * docs/frontend/routes.md#why-this-differs-from-the-example-route-list-in-the-step-10-brief.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type {
  ApplicationHistoryResponse,
  ApplicationResponse,
  UpdateApplicationStatusRequest,
} from './types'
import type { RefetchInterval } from '../hooks/usePolling'

export function listApplications(
  userId: string,
  status?: string,
): Promise<ApplicationResponse[]> {
  const query = status ? `?user_id=${userId}&status=${status}` : `?user_id=${userId}`
  return apiClient.get<ApplicationResponse[]>(`/applications${query}`)
}

export function getApplication(applicationId: string): Promise<ApplicationResponse> {
  return apiClient.get<ApplicationResponse>(`/applications/${applicationId}`)
}

export function updateApplicationStatus(
  applicationId: string,
  body: UpdateApplicationStatusRequest,
): Promise<ApplicationResponse> {
  return apiClient.patch<ApplicationResponse>(`/applications/${applicationId}/status`, body)
}

export function getApplicationHistory(
  applicationId: string,
): Promise<ApplicationHistoryResponse[]> {
  return apiClient.get<ApplicationHistoryResponse[]>(`/applications/${applicationId}/history`)
}

/**
 * Backs both the Dashboard summary (10s) and the Opportunities list (5s) —
 * two different intervals over the same query shape per
 * async-workflows.md's table, so the caller supplies its own
 * `refetchInterval` rather than this hook picking one.
 */
export function useApplications(
  userId: string,
  status?: string,
  options?: { refetchInterval?: RefetchInterval<ApplicationResponse[]> },
) {
  return useQuery({
    queryKey: queryKeys.applications(userId, status),
    queryFn: () => listApplications(userId, status),
    enabled: Boolean(userId),
    refetchInterval: options?.refetchInterval,
  })
}

/**
 * `/opportunities/:applicationId` polls this at 3s until *this specific
 * application's* status reaches a terminal value — a stop condition that
 * depends on the fetched data, not just "is this hook always polled", so
 * the caller passes its own `refetchInterval` (e.g. built with
 * `pollUntil` from `hooks/usePolling.ts`) rather than this hook
 * hard-coding the terminal-status check itself.
 */
export function useApplication(
  applicationId: string,
  options?: { refetchInterval?: RefetchInterval<ApplicationResponse> },
) {
  return useQuery({
    queryKey: queryKeys.application(applicationId),
    queryFn: () => getApplication(applicationId),
    enabled: Boolean(applicationId),
    refetchInterval: options?.refetchInterval,
  })
}

/** Fetch-once + `refetchOnWindowFocus` — an audit trail, not an in-progress process. */
export function useApplicationHistory(applicationId: string) {
  return useQuery({
    queryKey: queryKeys.history(applicationId),
    queryFn: () => getApplicationHistory(applicationId),
    enabled: Boolean(applicationId),
  })
}

/**
 * `userId` isn't part of `UpdateApplicationStatusRequest`/the response, but
 * is needed to invalidate the scoped `applications(userId)` list — callers
 * (which already have the active user in context) supply it alongside the
 * path/body params.
 */
export function useUpdateApplicationStatus() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      applicationId,
      body,
    }: {
      applicationId: string
      userId: string
      body: UpdateApplicationStatusRequest
    }) => updateApplicationStatus(applicationId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.application(variables.applicationId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.applications(variables.userId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.history(variables.applicationId) })
    },
  })
}
