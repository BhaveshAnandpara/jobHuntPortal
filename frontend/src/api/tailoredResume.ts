/**
 * Resume Tailoring Service — `src/resume_tailoring/api.py`'s three routes,
 * see docs/tailored-resume/TECH.md Task 6/7.
 *
 * Shape follows `matching.ts`/`outreach.ts` exactly: bare request functions
 * first, then the hooks that wrap them, keys from `queryKeys` only.
 *
 * Three things are worth knowing before reading the hooks:
 *
 * 1. **`POST` has two success outcomes, not one.** 201 means a document was
 *    created; 200 means the tailored version didn't clear the backend's
 *    5-point gate, which is an honest answer rather than a failure. Both
 *    arrive here as a resolved `TailoringResponse` (the client only throws
 *    on non-2xx), distinguished by `improved`. Nothing in this file treats
 *    `improved: false` as an error.
 *
 * 2. **`GET` 404s when nothing has been generated yet**, the same
 *    "unset is not an error" convention `GET /users/me/llm-config` and
 *    `GET /users/me/preferences` already use. It still surfaces as an
 *    `ApiError` because `apiClient` throws on every non-2xx, so consumers
 *    read it the way `llmConfigReadiness.ts` does —
 *    `toApiError(query.error).status === 404`. The app-wide retry policy
 *    (`app/queryClient.ts`) never retries a 4xx, so "not generated yet"
 *    settles on the first response instead of retrying twice first.
 *
 * 3. **The download is a binary route behind a Bearer token.** It cannot be
 *    a link the browser follows on its own, so it goes through
 *    `apiClient.file()` (same auth and error contract as every other call)
 *    and then `saveFileToDisk`. It is modelled as a mutation because it is
 *    a user-triggered action with its own pending/error state, not a cached
 *    server value — nothing would ever read a `Blob` back out of the query
 *    cache.
 *
 * Owner: frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { TailoredResumeSummary, TailoringResponse } from './types'
import { saveFileToDisk } from '../utils/download'

export function generateTailoredResume(jobId: string): Promise<TailoringResponse> {
  return apiClient.post<TailoringResponse>(`/jobs/${jobId}/tailored-resume`)
}

export function getTailoredResume(jobId: string): Promise<TailoredResumeSummary> {
  return apiClient.get<TailoredResumeSummary>(`/jobs/${jobId}/tailored-resume`)
}

/**
 * Fetches the `.docx` and hands it to the browser to save. Falls back to a
 * locally-built filename only if the response carried no
 * `Content-Disposition` — the server's own name wins whenever it sent one,
 * so the two can't drift.
 */
export async function downloadTailoredResume(jobId: string): Promise<void> {
  const { blob, filename } = await apiClient.file(`/jobs/${jobId}/tailored-resume/download`)
  saveFileToDisk(blob, filename ?? `tailored-resume-${jobId}.docx`)
}

/**
 * Fetch-once + `refetchOnWindowFocus`. The record only ever changes in
 * reaction to `useGenerateTailoredResume` (there is no backend process that
 * creates one in the background — generation is user-initiated by design,
 * docs/tailored-resume/PRODUCT.md), so there is no polling interval.
 */
export function useTailoredResume(
  jobId: string,
  options?: Pick<UseQueryOptions<TailoredResumeSummary, Error>, 'enabled'>,
) {
  return useQuery({
    queryKey: queryKeys.tailoredResume(jobId),
    queryFn: () => getTailoredResume(jobId),
    enabled: Boolean(jobId) && (options?.enabled ?? true),
  })
}

/**
 * Invalidates only this job's tailored-resume entry. Generation writes one
 * `tailored_resumes` row and touches nothing else observable from the
 * frontend — it does not change the `Application`, the `JobMatch`, or the
 * profile list (the transparent re-parse of Task 2 enriches a
 * `CandidateProfile` in place, but changes none of the fields any cached
 * view of it displays), so invalidating those would only trigger refetches
 * of data this call provably didn't change.
 */
export function useGenerateTailoredResume(jobId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => generateTailoredResume(jobId),
    onSuccess: (result) => {
      // Only a run that actually produced a document changed server state;
      // a below-the-gate run persists nothing, so there is nothing to
      // refetch and re-running the 404 would just add a request.
      if (result.improved) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tailoredResume(jobId) })
      }
    },
  })
}

export function useDownloadTailoredResume(jobId: string) {
  return useMutation({
    mutationFn: () => downloadTailoredResume(jobId),
  })
}
