/**
 * Resume/Profile Service (resumes) — functions and hooks, including the
 * base64 upload wire format and the `useResumes` poll-until-terminal
 * behavior. See docs/frontend/api-mapping.md#resumeprofile-service and
 * docs/frontend/async-workflows.md#resume-parsing-progressive-disclosure.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { deleteResume, listResumes, uploadResume, useDeleteResume, useResumes, useUploadResume } from './resumes'
import { useProfiles } from './profiles'
import { createWrapper } from './test-utils'

function makeFile(name = 'resume.pdf', content = '%PDF-1.4 fake') {
  return new File([content], name, { type: 'application/pdf' })
}

describe('resumes.ts functions', () => {
  it('listResumes resolves the list for the authenticated user', async () => {
    const result = await listResumes()

    expect(result).toHaveLength(1)
    expect(result[0].user_id).toBe('user-1')
  })

  it('uploadResume base64-encodes the file and posts it as JSON (not FormData)', async () => {
    let receivedBody: Record<string, unknown> | undefined
    let receivedContentType: string | null = null
    server.use(
      http.post(`${API_BASE_URL}/resumes`, async ({ request }) => {
        receivedContentType = request.headers.get('content-type')
        receivedBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          { id: 'resume-2', user_id: 'user-1', file_name: 'resume.pdf', status: 'UPLOADED', uploaded_at: '2026-01-02T00:00:00Z' },
          { status: 202 },
        )
      }),
    )

    const result = await uploadResume(makeFile())

    expect(receivedContentType).toContain('application/json')
    expect(receivedBody?.file_name).toBe('resume.pdf')
    expect(typeof receivedBody?.file_content).toBe('string')
    expect(receivedBody?.file_content).toBe(btoa('%PDF-1.4 fake'))
    expect(result.status).toBe('UPLOADED')
  })

  it('uploadResume surfaces a 400 VALIDATION_ERROR as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'file_content is required' } }, { status: 400 }),
      ),
    )

    const error = await uploadResume(makeFile()).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('VALIDATION_ERROR')
  })

  it('uploadResume surfaces a 404 as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'User not found' } }, { status: 404 }),
      ),
    )

    const error = await uploadResume(makeFile()).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('deleteResume resolves on 204', async () => {
    await expect(deleteResume('resume-1')).resolves.toBeUndefined()
  })

  it('propagates a network failure from listResumes as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/resumes`, () => HttpResponse.error()))

    const error = await listResumes().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('resumes.ts hooks', () => {
  it('useResumes resolves the list', async () => {
    const { result } = renderHook(() => useResumes(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useResumes keeps polling while a resume is non-terminal (PARSING) and stops once terminal', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let callCount = 0
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        callCount += 1
        const status = callCount === 1 ? 'PARSING' : 'PARSED'
        return HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'r.pdf', status, uploaded_at: '2026-01-01T00:00:00Z' },
        ])
      }),
    )

    const { result } = renderHook(() => useResumes(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.[0].status).toBe('PARSING')
    expect(callCount).toBe(1)

    await vi.advanceTimersByTimeAsync(2100)
    await waitFor(() => expect(result.current.data?.[0].status).toBe('PARSED'))
    expect(callCount).toBe(2)

    // Terminal now — advancing well past another interval should not trigger a third fetch.
    await vi.advanceTimersByTimeAsync(5000)
    expect(callCount).toBe(2)

    vi.useRealTimers()
  })

  it('useResumes keeps polling through a transient empty response for a resume id the caller is still waiting to observe (Step 12 first-upload regression)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let callCount = 0
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        callCount += 1
        // Call 1: the backend accepted the upload (202, id "resume-9") but
        // the row isn't visible to this read yet — the upload transaction
        // commits only once background parsing finishes (see
        // src/profiles/api/dependencies.py:get_session), so a real
        // GET /resumes landing in that window can legitimately return [].
        if (callCount === 1) return HttpResponse.json([])
        // Call 2: now visible, still PARSING.
        if (callCount === 2) {
          return HttpResponse.json([
            { id: 'resume-9', user_id: 'user-1', file_name: 'r.pdf', status: 'PARSING', uploaded_at: '2026-01-01T00:00:00Z' },
          ])
        }
        // Call 3+: terminal.
        return HttpResponse.json([
          { id: 'resume-9', user_id: 'user-1', file_name: 'r.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
        ])
      }),
    )

    const { result, rerender } = renderHook(
      ({ pendingResumeIds }: { pendingResumeIds?: ReadonlySet<string> }) => useResumes({ pendingResumeIds }),
      { wrapper: createWrapper(), initialProps: {} },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
    expect(callCount).toBe(1)

    // The caller (ResumesPage) now knows about the just-uploaded id —
    // mirrors it being added to `trackedResumeIds` right after
    // `uploadMutation` resolves.
    rerender({ pendingResumeIds: new Set(['resume-9']) })

    // Without the fix, `allResumesTerminal([])` alone would already have
    // stopped polling for good after call 1 — this proves it kept going.
    await vi.advanceTimersByTimeAsync(2100)
    await waitFor(() => expect(callCount).toBe(2))
    expect(result.current.data?.[0].status).toBe('PARSING')

    await vi.advanceTimersByTimeAsync(2100)
    await waitFor(() => expect(result.current.data?.[0].status).toBe('PARSED'))
    expect(callCount).toBe(3)

    // Terminal now — advancing well past another interval should not
    // trigger a fourth fetch.
    await vi.advanceTimersByTimeAsync(5000)
    expect(callCount).toBe(3)

    vi.useRealTimers()
  })

  it('useUploadResume invalidates resumes and profiles queries on success', async () => {
    let resumesCalls = 0
    let profilesCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        resumesCalls += 1
        return HttpResponse.json([])
      }),
      http.get(`${API_BASE_URL}/profiles`, () => {
        profilesCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: resumesResult } = renderHook(() => useResumes(), { wrapper })
    const { result: profilesResult } = renderHook(() => useProfiles(), { wrapper })
    await waitFor(() => expect(resumesResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(profilesResult.current.isSuccess).toBe(true))
    expect(resumesCalls).toBe(1)
    expect(profilesCalls).toBe(1)

    const { result: uploadResult } = renderHook(() => useUploadResume(), { wrapper })
    uploadResult.current.mutate(makeFile())

    await waitFor(() => expect(uploadResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(resumesCalls).toBe(2))
    await waitFor(() => expect(profilesCalls).toBe(2))
  })

  it('useDeleteResume invalidates resumes and profiles queries on success', async () => {
    let resumesCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        resumesCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: resumesResult } = renderHook(() => useResumes(), { wrapper })
    await waitFor(() => expect(resumesResult.current.isSuccess).toBe(true))
    expect(resumesCalls).toBe(1)

    const { result: deleteResult } = renderHook(() => useDeleteResume(), { wrapper })
    deleteResult.current.mutate({ resumeId: 'resume-1' })

    await waitFor(() => expect(deleteResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(resumesCalls).toBe(2))
  })
})
