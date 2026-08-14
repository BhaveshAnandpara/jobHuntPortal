/**
 * Job Matching Service — functions and hooks. See
 * docs/frontend/api-mapping.md#job-matching-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { getJobMatch, useJobMatch } from './matching'
import { createWrapper } from './test-utils'

describe('matching.ts functions', () => {
  it('getJobMatch resolves a JobMatchResponse carrying profile_scores (Step 10.5)', async () => {
    const result = await getJobMatch('job-1')
    expect(result.profile_scores).toHaveLength(1)
    expect(result.recommendation).toBe('SHORTLIST')
  })

  it('getJobMatch surfaces a 404 as ApiError', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/matches`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'No match for this job' } }, { status: 404 }),
      ),
    )

    const error = await getJobMatch('missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('propagates a 5xx as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId/matches`, () => new HttpResponse(null, { status: 500 })))

    const error = await getJobMatch('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId/matches`, () => HttpResponse.error()))

    const error = await getJobMatch('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('matching.ts hooks', () => {
  it('useJobMatch resolves and does not fire when jobId is empty', async () => {
    const { result: empty } = renderHook(() => useJobMatch(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useJobMatch('job-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.job_match_id).toBe('match-1')
  })
})
