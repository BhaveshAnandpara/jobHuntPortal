/**
 * Resume/Profile Service (resumes) — see
 * docs/frontend/api-mapping.md#resumeprofile-service. Owner:
 * frontend-api-agent.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { queryKeys } from './queryKeys'
import type { ResumeResponse, ResumeStatus } from './types'
import { fileToBase64 } from '../utils/base64'
import { pollUntil } from '../hooks/usePolling'

export function listResumes(userId: string): Promise<ResumeResponse[]> {
  return apiClient.get<ResumeResponse[]>(`/resumes?user_id=${userId}`)
}

/**
 * Encodes `file` as base64 and submits it — see the wire-format note in
 * docs/frontend/api-mapping.md and src/utils/base64.ts. Callers pass a
 * plain `File` (e.g. from an `<input type="file">`), never a pre-encoded
 * string, so this stays the single place that encoding happens.
 */
export async function uploadResume(userId: string, file: File): Promise<ResumeResponse> {
  const fileContent = await fileToBase64(file)
  return apiClient.post<ResumeResponse>('/resumes', {
    user_id: userId,
    file_name: file.name,
    file_content: fileContent,
  })
}

export function deleteResume(resumeId: string): Promise<void> {
  return apiClient.delete(`/resumes/${resumeId}`)
}

/** `ResumeStatus` values docs/frontend/async-workflows.md#resume-parsing calls terminal. */
const TERMINAL_RESUME_STATUSES: ReadonlySet<ResumeStatus> = new Set(['PARSED', 'PARSE_FAILED'])

function allResumesTerminal(resumes: ResumeResponse[] | undefined): boolean {
  if (!resumes) return false
  return resumes.every((resume) => TERMINAL_RESUME_STATUSES.has(resume.status))
}

/**
 * `allResumesTerminal` is vacuously `true` for `[]` (`[].every(...)` is
 * always `true`) — correct when the user genuinely has zero resumes, but
 * wrong immediately after an upload: the backend's upload transaction
 * commits the new row only once its background parsing step finishes (see
 * `src/profiles/api/dependencies.py:get_session`), so a poll that lands in
 * that window can legitimately observe `[]` for a resume that was already
 * accepted. Without this check, that transient `[]` reads as "nothing left
 * to poll for" and polling stops for good (Step 12 finding — a caller's
 * own upload can silently strand it). `pendingResumeIds` — ids the caller
 * knows it just uploaded but hasn't necessarily seen in a fetched list yet
 * — must each be present before the list counts as caught up.
 */
function allPendingResumesObserved(
  resumes: ResumeResponse[] | undefined,
  pendingResumeIds: ReadonlySet<string> | undefined,
): boolean {
  if (!pendingResumeIds || pendingResumeIds.size === 0) return true
  if (!resumes) return false
  const observedIds = new Set(resumes.map((resume) => resume.id))
  for (const id of pendingResumeIds) {
    if (!observedIds.has(id)) return false
  }
  return true
}

/**
 * Polls every 2s (docs/frontend/async-workflows.md's `/resumes` row) until
 * every resume's `status` is terminal (`PARSED`/`PARSE_FAILED`) AND every
 * caller-supplied `pendingResumeIds` id has actually shown up in a fetched
 * list, then stops automatically — this is the one polling case unambiguous
 * enough to wire at the hook level rather than leave to the caller (contrast
 * `useApplications`/`useOutreachList`, whose interval genuinely depends on
 * which page is asking). `pendingResumeIds` is optional and only needed by
 * callers tracking an in-flight upload (see `allPendingResumesObserved`
 * above for why); omitting it preserves the original list-only behavior.
 */
export function useResumes(userId: string, options?: { pendingResumeIds?: ReadonlySet<string> }) {
  const pendingResumeIds = options?.pendingResumeIds
  return useQuery({
    queryKey: queryKeys.resumes(userId),
    queryFn: () => listResumes(userId),
    enabled: Boolean(userId),
    refetchInterval: pollUntil(
      2000,
      (resumes) => allResumesTerminal(resumes) && allPendingResumesObserved(resumes, pendingResumeIds),
    ),
  })
}

export function useUploadResume() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, file }: { userId: string; file: File }) => uploadResume(userId, file),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.resumes(variables.userId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.profiles(variables.userId) })
    },
  })
}

/**
 * `deleteResume` only needs `resumeId` on the wire, but invalidation needs
 * `userId` to build the scoped `resumes`/`profiles` keys — callers (which
 * already have the active user in context) supply both.
 */
export function useDeleteResume() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ resumeId }: { resumeId: string; userId: string }) => deleteResume(resumeId),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.resumes(variables.userId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.profiles(variables.userId) })
    },
  })
}
