/**
 * Outreach Service — see docs/frontend/api-mapping.md#outreach-service.
 * Owner: frontend-api-agent.
 *
 * There is no `sendOutreach` function here, deliberately — there is no
 * send endpoint. Sending is a Kafka-triggered backend side effect of
 * approval; see docs/frontend/architecture.md#thin-client-principle.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient, toApiError } from './client'
import { queryKeys } from './queryKeys'
import type { ApproveOutreachRequest, EditOutreachRequest, OutreachResponse } from './types'
import type { RefetchInterval } from '../hooks/usePolling'

export function listOutreach(status?: string): Promise<OutreachResponse[]> {
  const query = status ? `?status=${status}` : ''
  return apiClient.get<OutreachResponse[]>(`/outreach${query}`)
}

export function getOutreach(outreachId: string): Promise<OutreachResponse> {
  return apiClient.get<OutreachResponse>(`/outreach/${outreachId}`)
}

export function approveOutreach(
  outreachId: string,
  body: ApproveOutreachRequest,
): Promise<OutreachResponse> {
  return apiClient.post<OutreachResponse>(`/outreach/${outreachId}/approve`, body)
}

export function rejectOutreach(outreachId: string): Promise<OutreachResponse> {
  return apiClient.post<OutreachResponse>(`/outreach/${outreachId}/reject`)
}

export function editOutreach(
  outreachId: string,
  body: EditOutreachRequest,
): Promise<OutreachResponse> {
  return apiClient.post<OutreachResponse>(`/outreach/${outreachId}/edit`, body)
}

/**
 * `status` genuinely changes the interval per page (the queue polls this
 * at 5s always-on with `status=PENDING_APPROVAL`; the Dashboard polls the
 * same shape at 10s) — see async-workflows.md's table — so the caller
 * controls `refetchInterval` rather than this hook hard-coding one.
 */
export function useOutreachList(
  status?: string,
  options?: { refetchInterval?: RefetchInterval<OutreachResponse[]> },
) {
  return useQuery({
    queryKey: queryKeys.outreachList(status),
    queryFn: () => listOutreach(status),
    refetchInterval: options?.refetchInterval,
  })
}

/** Fetch-once + `refetchOnWindowFocus` — changes only in reaction to approve/edit/reject. */
export function useOutreachItem(outreachId: string) {
  return useQuery({
    queryKey: queryKeys.outreachItem(outreachId),
    queryFn: () => getOutreach(outreachId),
    enabled: Boolean(outreachId),
  })
}

/**
 * On a `409` (already decided elsewhere — error-handling.md's specific
 * row), refetches the item instead of leaving stale state on screen; this
 * is a mechanical refetch, not a retry of the mutation itself, so it never
 * double-acts on stale intent. Feature code only needs to show
 * `error.message`, the refetch-to-current-state is handled here.
 */
function refetchOutreachItemOn409(
  queryClient: ReturnType<typeof useQueryClient>,
  error: unknown,
  outreachId: string,
): void {
  const apiError = toApiError(error)
  if (apiError.status === 409) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.outreachItem(outreachId) })
  }
}

/**
 * Invalidation reasoning (per the task brief's "use your judgment,
 * document your reasoning"): approve is the only one of the three actions
 * that emits a Kafka event (`OutreachApprovedEvent` —
 * api-contracts.md#post-outreachoutreach_idapprove) that a Tracking Service
 * consumer reacts to, asynchronously moving the linked `Application`
 * through `OUTREACH_APPROVED`/`OUTREACH_SENT`. So approve also invalidates
 * `queryKeys.applications()` and, when the caller supplies it,
 * `queryKeys.application(applicationId)` /
 * `queryKeys.history(applicationId)` — `OutreachResponse` itself carries no
 * `application_id` (only `job_id`, which isn't a lookup key for a specific
 * `Application`), so this hook cannot resolve that id on its own; a caller
 * that already has it (e.g. Opportunity Detail's outreach panel) may pass
 * it to get the more targeted invalidation too.
 */
export function useApproveOutreach() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      outreachId,
      body,
    }: {
      outreachId: string
      applicationId?: string
      body: ApproveOutreachRequest
    }) => approveOutreach(outreachId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachList() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachItem(variables.outreachId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.applications() })
      if (variables.applicationId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.application(variables.applicationId) })
        void queryClient.invalidateQueries({ queryKey: queryKeys.history(variables.applicationId) })
      }
    },
    onError: (error, variables) => {
      refetchOutreachItemOn409(queryClient, error, variables.outreachId)
    },
  })
}

/**
 * Reject, per api-contracts.md, generates **no event**
 * ("Events generated: none — see kafka-topics.md's note on the deferred
 * `outreach.rejected` topic"), so unlike approve there is no async path by
 * which a reject changes `Application` state — only `outreachList`/
 * `outreachItem` are invalidated, matching api-mapping.md's table exactly
 * and avoiding an invalidation (`applications`) that would just trigger an
 * unnecessary refetch of data reject provably didn't change.
 */
export function useRejectOutreach() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ outreachId }: { outreachId: string }) => rejectOutreach(outreachId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachList() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachItem(variables.outreachId) })
    },
    onError: (error, variables) => {
      refetchOutreachItemOn409(queryClient, error, variables.outreachId)
    },
  })
}

/**
 * Edit only updates `outreach.final_message`/`status=EDITED`
 * (api-contracts.md) and generates no event either — same reasoning as
 * reject, no `applications` invalidation.
 */
export function useEditOutreach() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      outreachId,
      body,
    }: {
      outreachId: string
      body: EditOutreachRequest
    }) => editOutreach(outreachId, body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachList() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.outreachItem(variables.outreachId) })
    },
    onError: (error, variables) => {
      refetchOutreachItemOn409(queryClient, error, variables.outreachId)
    },
  })
}
