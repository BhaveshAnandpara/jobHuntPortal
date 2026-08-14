/**
 * Route: /resumes — see docs/frontend/routes.md#resumes--resume-management,
 * docs/frontend/user-flows.md#multiple-resumes-ux, and
 * docs/frontend/async-workflows.md#resume-parsing-progressive-disclosure.
 *
 * Lists resumes (`useResumes`, already polling every 2s through
 * PARSING -> PARSED/PARSE_FAILED via the hook itself — no polling logic
 * needed here), cross-referenced client-side with `useProfiles` by
 * matching `ResumeProfile.resume_id` to `ResumeResponse.id`.
 *
 * Upload and delete are thin compositions of the already-built
 * `useUploadResume`/`useDeleteResume` hooks — this page never touches
 * base64 encoding itself (that's `api/resumes.ts`'s `uploadResume`).
 *
 * "Replace" has no dedicated backend endpoint (state-machines.md's resume
 * lifecycle: a retry/replacement is always a *new* Resume row, never a
 * re-attempt on the old one). The safe client-side sequence is upload the
 * new file FIRST, wait for it to succeed, and only then delete the old
 * resume — never delete-then-upload, so a failed replacement never leaves
 * the user with zero resumes.
 *
 * The UI stays profession-independent: skills/title/summary/seniority are
 * rendered as free text/lists exactly as `ResumeProfile` returns them, with
 * no section framed around any one profession.
 *
 * Owner: frontend-profile-agent.
 */

import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { toast } from 'sonner'
import { Button, Card, Dialog, EmptyState, ErrorState, PageHeader, Skeleton, StatusBadge } from '../../components'
import { useDeleteResume, useResumes, useUploadResume } from '../../api/resumes'
import { useProfiles } from '../../api/profiles'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import { formatDate } from '../../utils/format'
import type { ResumeProfile, ResumeResponse } from '../../api/types'

function buildProfilesByResumeId(profiles: ResumeProfile[] | undefined): Map<string, ResumeProfile> {
  const map = new Map<string, ResumeProfile>()
  for (const profile of profiles ?? []) {
    map.set(profile.resume_id, profile)
  }
  return map
}

export function ResumesPage() {
  const { userId } = useCurrentUserId()
  const activeUserId = userId ?? ''

  // Resume ids (from any successful upload, plain or replace) whose eventual
  // PARSED transition should trigger a `profiles` refetch — see the effect
  // below. A resume's profile is only guaranteed to exist once parsing
  // finishes successfully (routes.py commits profile + PARSED atomically),
  // so we can't rely on the upload-time invalidation alone. Also passed to
  // `useResumes` as `pendingResumeIds`: a resume just accepted by the
  // backend may not appear in a `GET /resumes` response for a brief window
  // (see resumes.ts's `allPendingResumesObserved`), and without this,
  // polling could read that transient gap as "nothing left to wait for" and
  // stop for good — most visible on a user's very first upload, where the
  // list would otherwise go straight from `[]` to `[]`.
  const [trackedResumeIds, setTrackedResumeIds] = useState<ReadonlySet<string>>(new Set())

  const resumesQuery = useResumes(activeUserId, { pendingResumeIds: trackedResumeIds })
  const profilesQuery = useProfiles(activeUserId)
  const uploadMutation = useUploadResume()
  const deleteMutation = useDeleteResume()

  const uploadInputRef = useRef<HTMLInputElement>(null)
  const replaceInputRef = useRef<HTMLInputElement>(null)
  // The specific "Delete" button that opened the confirm dialog, captured
  // from the click event itself — the dialog is shared across every row, so
  // this is what lets focus return to the correct row's button on close
  // (not just "a" Delete button). See components/Dialog.tsx's `triggerRef`.
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null)
  const [replaceTarget, setReplaceTarget] = useState<ResumeResponse | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ResumeResponse | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [isReplacing, setIsReplacing] = useState(false)
  // Resume replace: the new upload's id, plus which old resume to remove
  // once (and only once) that new resume reaches PARSED — see the
  // safe-replace effect below, which watches `resumesQuery.data` (already
  // polled every 2s by `useResumes`) for this id's status.
  const [awaitingReplace, setAwaitingReplace] = useState<{ newResumeId: string; oldResume: ResumeResponse } | null>(
    null,
  )

  const profilesByResumeId = buildProfilesByResumeId(profilesQuery.data)
  const resumes = resumesQuery.data ?? []
  const loadError = resumesQuery.isError ? toApiError(resumesQuery.error) : null

  // Reacts to `resumesQuery`'s existing 2s poll (no new polling added here).
  useEffect(() => {
    const polledResumes = resumesQuery.data
    if (!polledResumes) return

    if (trackedResumeIds.size > 0) {
      let profileMayExist = false
      const stillPending = new Set(trackedResumeIds)
      for (const id of trackedResumeIds) {
        const resume = polledResumes.find((candidate) => candidate.id === id)
        if (!resume) continue
        if (resume.status === 'PARSED') {
          profileMayExist = true
          stillPending.delete(id)
        } else if (resume.status === 'PARSE_FAILED') {
          stillPending.delete(id)
        }
      }
      if (stillPending.size !== trackedResumeIds.size) {
        setTrackedResumeIds(stillPending)
      }
      if (profileMayExist) {
        void profilesQuery.refetch()
      }
    }

    if (awaitingReplace) {
      const newResume = polledResumes.find((candidate) => candidate.id === awaitingReplace.newResumeId)
      if (newResume?.status === 'PARSED') {
        const { oldResume } = awaitingReplace
        setAwaitingReplace(null)
        void (async () => {
          try {
            await deleteMutation.mutateAsync({ resumeId: oldResume.id, userId: activeUserId })
            toast.success(`Replaced "${oldResume.file_name}".`)
          } catch (error) {
            const apiError = toApiError(error)
            toast.error(
              `The new resume was uploaded and analyzed successfully, but "${oldResume.file_name}" could not be removed automatically: ${apiError.message}`,
            )
          } finally {
            setIsReplacing(false)
          }
        })()
      } else if (newResume?.status === 'PARSE_FAILED') {
        const { oldResume } = awaitingReplace
        setAwaitingReplace(null)
        setIsReplacing(false)
        toast.error(
          `The replacement for "${oldResume.file_name}" could not be analyzed. The original resume was not removed.`,
        )
      }
    }
  }, [resumesQuery.data, awaitingReplace, trackedResumeIds, activeUserId, deleteMutation, profilesQuery])

  async function handleFilesSelected(files: File[]) {
    if (!activeUserId || files.length === 0) return
    setIsUploading(true)
    const results = await Promise.allSettled(
      files.map((file) => uploadMutation.mutateAsync({ userId: activeUserId, file })),
    )
    const newlyUploadedIds: string[] = []
    results.forEach((result, index) => {
      if (result.status === 'rejected') {
        const apiError = toApiError(result.reason)
        toast.error(`"${files[index].name}" failed to upload: ${apiError.message}`)
      } else {
        newlyUploadedIds.push(result.value.id)
      }
    })
    if (newlyUploadedIds.length > 0) {
      setTrackedResumeIds((prev) => new Set([...prev, ...newlyUploadedIds]))
    }
    setIsUploading(false)
  }

  function handleUploadInputChange(event: ChangeEvent<HTMLInputElement>) {
    // `event.target.files` is a live FileList in real browsers: resetting
    // `value` below clears it in place, so it must be snapshotted into an
    // owned array first (see file header / Step 12 notes).
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    void handleFilesSelected(files)
  }

  // Safe replace: upload the new file first; only once it reaches PARSED
  // (observed via the effect above, driven by `resumesQuery`'s existing
  // poll) do we delete the old resume. A 202 here only means "accepted for
  // background processing" (see src/profiles/api/routes.py's upload_resume)
  // — it is not proof the new resume parsed successfully, so `isReplacing`
  // stays true until that's confirmed one way or the other.
  async function performReplace(oldResume: ResumeResponse, file: File) {
    if (!activeUserId) return
    setIsReplacing(true)
    let uploaded: ResumeResponse
    try {
      uploaded = await uploadMutation.mutateAsync({ userId: activeUserId, file })
    } catch (error) {
      const apiError = toApiError(error)
      toast.error(
        `Could not upload the replacement for "${oldResume.file_name}": ${apiError.message}. The original resume was not removed.`,
      )
      setIsReplacing(false)
      return
    }

    setTrackedResumeIds((prev) => new Set(prev).add(uploaded.id))
    setAwaitingReplace({ newResumeId: uploaded.id, oldResume })
    // isReplacing intentionally stays true here — the polling effect above
    // clears it once the new resume reaches PARSED or PARSE_FAILED.
  }

  function handleReplaceInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    event.target.value = ''
    const target = replaceTarget
    setReplaceTarget(null)
    if (!file || !target) return
    void performReplace(target, file)
  }

  function confirmDelete() {
    if (!deleteTarget || !activeUserId) return
    const target = deleteTarget
    setDeleteTarget(null)
    deleteMutation.mutate(
      { resumeId: target.id, userId: activeUserId },
      {
        onSuccess: () => toast.success(`Deleted "${target.file_name}".`),
        onError: (error) => toast.error(toApiError(error).message),
      },
    )
  }

  return (
    <div>
      <PageHeader
        title="Resumes"
        description="Upload and manage your resumes. Each one is analyzed automatically to build a profile the platform can match against opportunities."
        action={
          <Button
            variant="primary"
            isLoading={isUploading}
            onClick={() => uploadInputRef.current?.click()}
          >
            Upload resume
          </Button>
        }
      />

      <input
        ref={uploadInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleUploadInputChange}
        aria-label="Upload resume files"
      />
      <input
        ref={replaceInputRef}
        type="file"
        className="hidden"
        onChange={handleReplaceInputChange}
        aria-label="Replacement resume file"
      />

      {resumesQuery.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : loadError ? (
        <ErrorState message={loadError.message} onRetry={() => void resumesQuery.refetch()} />
      ) : resumes.length === 0 ? (
        <EmptyState
          title="No resumes yet"
          description="Upload a resume to build your first profile."
          action={
            <Button variant="primary" onClick={() => uploadInputRef.current?.click()}>
              Upload resume
            </Button>
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {resumes.map((resume) => {
            const profile = profilesByResumeId.get(resume.id)
            return (
              <li key={resume.id}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-gray-900">{resume.file_name}</p>
                      <p className="text-xs text-gray-500">Uploaded {formatDate(resume.uploaded_at)}</p>
                    </div>
                    <StatusBadge status={resume.status} />
                  </div>

                  {profile ? (
                    <div className="mt-4 border-t border-gray-100 pt-4">
                      <p className="text-sm font-medium text-gray-900">{profile.title}</p>
                      {profile.seniority ? (
                        <p className="text-xs text-gray-500">{profile.seniority}</p>
                      ) : null}
                      {profile.summary ? (
                        <p className="mt-2 text-sm text-gray-600">{profile.summary}</p>
                      ) : null}
                      {profile.skills.length > 0 ? (
                        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Skills">
                          {profile.skills.map((skill) => (
                            <li
                              key={skill}
                              className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                            >
                              {skill}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : resume.status === 'PARSE_FAILED' ? (
                    <p className="mt-4 border-t border-gray-100 pt-4 text-sm text-gray-500">
                      This resume could not be analyzed. Upload a corrected file to try again.
                    </p>
                  ) : null}

                  <div className="mt-4 flex gap-2">
                    <Button
                      variant="secondary"
                      disabled={isReplacing}
                      onClick={() => {
                        setReplaceTarget(resume)
                        replaceInputRef.current?.click()
                      }}
                    >
                      Replace
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={(event) => {
                        deleteTriggerRef.current = event.currentTarget
                        setDeleteTarget(resume)
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </Card>
              </li>
            )
          })}
        </ul>
      )}

      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title="Delete resume"
        triggerRef={deleteTriggerRef}
        description={
          deleteTarget
            ? `Delete "${deleteTarget.file_name}"? This also removes its derived profile.`
            : undefined
        }
      >
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirmDelete} isLoading={deleteMutation.isPending}>
            Delete
          </Button>
        </div>
      </Dialog>
    </div>
  )
}
