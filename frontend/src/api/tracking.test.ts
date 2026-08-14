/**
 * Tracking Service — functions and hooks, including the invalid-transition
 * (400) case on `PATCH /applications/{id}/status` and the page-controllable
 * `refetchInterval` option on `useApplications`/`useApplication`. See
 * docs/frontend/api-mapping.md#tracking-service and
 * docs/frontend/error-handling.md#invalid-state-transition-ux-a-specific-case-of-400-validation_error.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import {
  getApplication,
  getApplicationHistory,
  listApplications,
  updateApplicationStatus,
  useApplication,
  useApplicationHistory,
  useApplications,
  useUpdateApplicationStatus,
} from './tracking'
import { createWrapper } from './test-utils'

describe('tracking.ts functions', () => {
  it('listApplications resolves the list, with and without a status filter', async () => {
    let lastUrl: string | undefined
    server.use(
      http.get(`${API_BASE_URL}/applications`, ({ request }) => {
        lastUrl = request.url
        return HttpResponse.json([])
      }),
    )

    await listApplications('user-1')
    expect(lastUrl).toContain('user_id=user-1')
    expect(lastUrl).not.toContain('status=')

    await listApplications('user-1', 'SHORTLISTED')
    expect(lastUrl).toContain('status=SHORTLISTED')
  })

  it('getApplication resolves one application', async () => {
    const result = await getApplication('app-1')
    expect(result.id).toBe('app-1')
  })

  it('getApplication surfaces a 404 as ApiError', async () => {
    server.use(
      http.get(`${API_BASE_URL}/applications/:applicationId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Application not found' } }, { status: 404 }),
      ),
    )

    const error = await getApplication('missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('updateApplicationStatus resolves 200 on a valid transition', async () => {
    const result = await updateApplicationStatus('app-1', { new_status: 'APPLIED', applied_date: null, notes: null })
    expect(result.status).toBe('APPLIED')
  })

  it('updateApplicationStatus surfaces a 400 VALIDATION_ERROR on an invalid transition', async () => {
    server.use(
      http.patch(`${API_BASE_URL}/applications/:applicationId/status`, () =>
        HttpResponse.json(
          { detail: { code: 'VALIDATION_ERROR', message: 'Cannot transition from DISCOVERED to OFFER' } },
          { status: 400 },
        ),
      ),
    )

    const error = await updateApplicationStatus('app-1', { new_status: 'OFFER', applied_date: null, notes: null }).catch(
      (e: unknown) => e,
    )

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('VALIDATION_ERROR')
    expect((error as ApiError).message).toContain('Cannot transition')
  })

  it('getApplicationHistory resolves the audit trail', async () => {
    const result = await getApplicationHistory('app-1')
    expect(result).toHaveLength(1)
    expect(result[0].to_status).toBe('DISCOVERED')
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/applications`, () => HttpResponse.error()))

    const error = await listApplications('user-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('tracking.ts hooks', () => {
  it('useApplications resolves and does not fire when userId is empty', async () => {
    const { result: empty } = renderHook(() => useApplications(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useApplications('user-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useApplications honors a caller-supplied refetchInterval (page-specific polling)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let callCount = 0
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => {
        callCount += 1
        return HttpResponse.json([])
      }),
    )

    const { result } = renderHook(() => useApplications('user-1', undefined, { refetchInterval: 5000 }), {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(callCount).toBe(1)

    await vi.advanceTimersByTimeAsync(5100)
    await waitFor(() => expect(callCount).toBe(2))

    vi.useRealTimers()
  })

  it('useApplications with no refetchInterval option does not poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let callCount = 0
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => {
        callCount += 1
        return HttpResponse.json([])
      }),
    )

    const { result } = renderHook(() => useApplications('user-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(callCount).toBe(1)

    await vi.advanceTimersByTimeAsync(20000)
    expect(callCount).toBe(1)

    vi.useRealTimers()
  })

  it('useApplication resolves and does not fire when applicationId is empty', async () => {
    const { result: empty } = renderHook(() => useApplication(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useApplication('app-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe('app-1')
  })

  it('useApplicationHistory resolves the audit trail', async () => {
    const { result } = renderHook(() => useApplicationHistory('app-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useUpdateApplicationStatus invalidates application, applications(userId), and history on success', async () => {
    let applicationCalls = 0
    let applicationsCalls = 0
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
          status: 'SHORTLISTED',
          selected_resume_id: null,
          match_score: null,
          matched_skills: [],
          missing_skills: [],
          discovered_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        })
      }),
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
      http.get(`${API_BASE_URL}/applications/:applicationId/history`, () => {
        historyCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: appResult } = renderHook(() => useApplication('app-1'), { wrapper })
    const { result: appsResult } = renderHook(() => useApplications('user-1'), { wrapper })
    const { result: historyResult } = renderHook(() => useApplicationHistory('app-1'), { wrapper })
    await waitFor(() => expect(appResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(historyResult.current.isSuccess).toBe(true))
    expect(applicationCalls).toBe(1)
    expect(applicationsCalls).toBe(1)
    expect(historyCalls).toBe(1)

    const { result: updateResult } = renderHook(() => useUpdateApplicationStatus(), { wrapper })
    updateResult.current.mutate({
      applicationId: 'app-1',
      userId: 'user-1',
      body: { new_status: 'APPLIED', applied_date: null, notes: null },
    })

    await waitFor(() => expect(updateResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(applicationCalls).toBe(2))
    await waitFor(() => expect(applicationsCalls).toBe(2))
    await waitFor(() => expect(historyCalls).toBe(2))
  })

  it('useUpdateApplicationStatus surfaces a 400 invalid-transition error on the mutation result', async () => {
    server.use(
      http.patch(`${API_BASE_URL}/applications/:applicationId/status`, () =>
        HttpResponse.json(
          { detail: { code: 'VALIDATION_ERROR', message: 'Cannot transition from DISCOVERED to OFFER' } },
          { status: 400 },
        ),
      ),
    )
    const { result } = renderHook(() => useUpdateApplicationStatus(), { wrapper: createWrapper() })

    result.current.mutate({
      applicationId: 'app-1',
      userId: 'user-1',
      body: { new_status: 'OFFER', applied_date: null, notes: null },
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as ApiError).code).toBe('VALIDATION_ERROR')
  })
})
