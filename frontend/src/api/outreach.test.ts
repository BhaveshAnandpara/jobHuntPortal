/**
 * Outreach Service — functions and hooks, including the 409-conflict
 * refetch behavior and the approve/reject/edit invalidation reasoning
 * documented in outreach.ts (approve alone touches `applications` because
 * it's the only one of the three that emits a Kafka event per
 * api-contracts.md). See docs/frontend/error-handling.md's 409 row and
 * docs/frontend/api-mapping.md#outreach-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import {
  approveOutreach,
  editOutreach,
  getOutreach,
  listOutreach,
  rejectOutreach,
  useApproveOutreach,
  useEditOutreach,
  useOutreachItem,
  useOutreachList,
  useRejectOutreach,
} from './outreach'
import { useApplications } from './tracking'
import { createWrapper } from './test-utils'

describe('outreach.ts functions', () => {
  it('listOutreach resolves the list, with and without a status filter', async () => {
    let lastUrl: string | undefined
    server.use(
      http.get(`${API_BASE_URL}/outreach`, ({ request }) => {
        lastUrl = request.url
        return HttpResponse.json([])
      }),
    )

    await listOutreach()
    expect(lastUrl).not.toContain('status=')

    await listOutreach('PENDING_APPROVAL')
    expect(lastUrl).toContain('status=PENDING_APPROVAL')
  })

  it('getOutreach resolves one record', async () => {
    const result = await getOutreach('outreach-1')
    expect(result.id).toBe('outreach-1')
  })

  it('approveOutreach resolves 200 on success', async () => {
    const result = await approveOutreach('outreach-1', { final_message: null })
    expect(result.status).toBe('APPROVED')
  })

  it('approveOutreach surfaces a 409 (already decided) as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )

    const error = await approveOutreach('outreach-1', { final_message: null }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
  })

  it('rejectOutreach surfaces a 409 as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/reject`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )

    const error = await rejectOutreach('outreach-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
  })

  it('editOutreach surfaces a 400 VALIDATION_ERROR as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/:outreachId/edit`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'message is required' } }, { status: 400 }),
      ),
    )

    const error = await editOutreach('outreach-1', { message: '' }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('VALIDATION_ERROR')
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/outreach/:outreachId`, () => HttpResponse.error()))

    const error = await getOutreach('outreach-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('outreach.ts hooks', () => {
  it('useOutreachList resolves the list', async () => {
    const { result } = renderHook(() => useOutreachList(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useOutreachItem resolves one item and does not fire when outreachId is empty', async () => {
    const { result: empty } = renderHook(() => useOutreachItem(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useOutreachItem('outreach-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe('outreach-1')
  })

  it('useApproveOutreach invalidates outreachList, outreachItem, and applications() on success', async () => {
    let outreachListCalls = 0
    let outreachItemCalls = 0
    let applicationsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () => {
        outreachListCalls += 1
        return HttpResponse.json([])
      }),
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        outreachItemCalls += 1
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi',
          final_message: null,
          status: 'PENDING_APPROVAL',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: null,
          sent_at: null,
        })
      }),
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: listResult } = renderHook(() => useOutreachList(), { wrapper })
    const { result: itemResult } = renderHook(() => useOutreachItem('outreach-1'), { wrapper })
    const { result: appsResult } = renderHook(() => useApplications(), { wrapper })
    await waitFor(() => expect(listResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(itemResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    expect(outreachListCalls).toBe(1)
    expect(outreachItemCalls).toBe(1)
    expect(applicationsCalls).toBe(1)

    const { result: approveResult } = renderHook(() => useApproveOutreach(), { wrapper })
    approveResult.current.mutate({ outreachId: 'outreach-1', body: { final_message: null } })

    await waitFor(() => expect(approveResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(outreachListCalls).toBe(2))
    await waitFor(() => expect(outreachItemCalls).toBe(2))
    await waitFor(() => expect(applicationsCalls).toBe(2))
  })

  it('useApproveOutreach also invalidates a caller-supplied application(applicationId)/history(applicationId)', async () => {
    let applicationCalls = 0
    let historyCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () => {
        applicationCalls += 1
        return HttpResponse.json({
          id: 'app-1',
          job_id: 'job-1',
          user_id: 'user-1',
          company: 'Acme',
          title: 'Engineer',
          status: 'OUTREACH_GENERATED',
          selected_resume_id: null,
          match_score: null,
          matched_skills: [],
          missing_skills: [],
          discovered_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        })
      }),
      http.get(`${API_BASE_URL}/applications/:applicationId/history`, () => {
        historyCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { useApplication, useApplicationHistory } = await import('./tracking')
    const { result: applicationResult } = renderHook(() => useApplication('app-1'), { wrapper })
    const { result: historyResult } = renderHook(() => useApplicationHistory('app-1'), { wrapper })
    await waitFor(() => expect(applicationResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(historyResult.current.isSuccess).toBe(true))
    expect(applicationCalls).toBe(1)
    expect(historyCalls).toBe(1)

    const { result: approveResult } = renderHook(() => useApproveOutreach(), { wrapper })
    approveResult.current.mutate({
      outreachId: 'outreach-1',
      applicationId: 'app-1',
      body: { final_message: null },
    })

    await waitFor(() => expect(approveResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(applicationCalls).toBe(2))
    await waitFor(() => expect(historyCalls).toBe(2))
  })

  it('useApproveOutreach on 409 refetches the item instead of leaving stale state', async () => {
    let outreachItemCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        outreachItemCalls += 1
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi',
          final_message: null,
          status: 'REJECTED',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: '2026-01-01T00:05:00Z',
          sent_at: null,
        })
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/approve`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    const wrapper = createWrapper()
    const { result: itemResult } = renderHook(() => useOutreachItem('outreach-1'), { wrapper })
    await waitFor(() => expect(itemResult.current.isSuccess).toBe(true))
    expect(outreachItemCalls).toBe(1)

    const { result: approveResult } = renderHook(() => useApproveOutreach(), { wrapper })
    approveResult.current.mutate({ outreachId: 'outreach-1', body: { final_message: null } })

    await waitFor(() => expect(approveResult.current.isError).toBe(true))
    expect((approveResult.current.error as ApiError).status).toBe(409)
    await waitFor(() => expect(outreachItemCalls).toBe(2))
  })

  it('useRejectOutreach does NOT invalidate applications (reject generates no event)', async () => {
    let applicationsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: appsResult } = renderHook(() => useApplications(), { wrapper })
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    expect(applicationsCalls).toBe(1)

    const { result: rejectResult } = renderHook(() => useRejectOutreach(), { wrapper })
    rejectResult.current.mutate({ outreachId: 'outreach-1' })

    await waitFor(() => expect(rejectResult.current.isSuccess).toBe(true))
    // Give any (incorrect) invalidation a chance to fire before asserting it didn't.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(applicationsCalls).toBe(1)
  })

  it('useRejectOutreach on 409 refetches the item', async () => {
    let outreachItemCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach/:outreachId`, () => {
        outreachItemCalls += 1
        return HttpResponse.json({
          id: 'outreach-1',
          job_id: 'job-1',
          contact_id: 'contact-1',
          channel: 'EMAIL',
          draft_message: 'Hi',
          final_message: null,
          status: 'APPROVED',
          generated_at: '2026-01-01T00:00:00Z',
          decided_at: '2026-01-01T00:05:00Z',
          sent_at: null,
        })
      }),
      http.post(`${API_BASE_URL}/outreach/:outreachId/reject`, () =>
        HttpResponse.json({ detail: { code: 'CONFLICT', message: 'This was already decided' } }, { status: 409 }),
      ),
    )
    const wrapper = createWrapper()
    const { result: itemResult } = renderHook(() => useOutreachItem('outreach-1'), { wrapper })
    await waitFor(() => expect(itemResult.current.isSuccess).toBe(true))
    expect(outreachItemCalls).toBe(1)

    const { result: rejectResult } = renderHook(() => useRejectOutreach(), { wrapper })
    rejectResult.current.mutate({ outreachId: 'outreach-1' })

    await waitFor(() => expect(rejectResult.current.isError).toBe(true))
    await waitFor(() => expect(outreachItemCalls).toBe(2))
  })

  it('useEditOutreach invalidates outreachList/outreachItem but not applications', async () => {
    let outreachListCalls = 0
    let applicationsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/outreach`, () => {
        outreachListCalls += 1
        return HttpResponse.json([])
      }),
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: listResult } = renderHook(() => useOutreachList(), { wrapper })
    const { result: appsResult } = renderHook(() => useApplications(), { wrapper })
    await waitFor(() => expect(listResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    expect(outreachListCalls).toBe(1)
    expect(applicationsCalls).toBe(1)

    const { result: editResult } = renderHook(() => useEditOutreach(), { wrapper })
    editResult.current.mutate({ outreachId: 'outreach-1', body: { message: 'Updated' } })

    await waitFor(() => expect(editResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(outreachListCalls).toBe(2))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(applicationsCalls).toBe(1)
  })
})
