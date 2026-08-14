/**
 * Job Ingestion Service — functions and hooks. See
 * docs/frontend/api-mapping.md#job-ingestion-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { getJob, ingestJobUrl, useIngestJobUrl, useJob } from './jobs'
import { useApplications } from './tracking'
import { createWrapper } from './test-utils'

describe('jobs.ts functions', () => {
  it('ingestJobUrl posts and resolves the created JobResponse (202)', async () => {
    const result = await ingestJobUrl({ user_id: 'user-1', url: 'https://boards.example.com/jobs/1' })
    expect(result.company).toBe('Acme Robotics')
  })

  it('ingestJobUrl surfaces a 400 INVALID_JOB_URL as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/ingest-url`, () =>
        HttpResponse.json({ detail: { code: 'INVALID_JOB_URL', message: 'Not a recognized job posting URL' } }, { status: 400 }),
      ),
    )

    const error = await ingestJobUrl({ user_id: 'user-1', url: 'not-a-url' }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('INVALID_JOB_URL')
  })

  it('getJob resolves a job with the Step 10.5 fields present', async () => {
    const result = await getJob('job-1')
    expect(result.location).toBeDefined()
    expect(result.extracted_skills).toBeDefined()
    expect(result.source_url).toBeDefined()
  })

  it('getJob surfaces a 404 as ApiError', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Job not found' } }, { status: 404 }),
      ),
    )

    const error = await getJob('missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId`, () => HttpResponse.error()))

    const error = await getJob('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('jobs.ts hooks', () => {
  it('useJob resolves and does not fire when jobId is empty', async () => {
    const { result: empty } = renderHook(() => useJob(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useJob('job-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe('job-1')
  })

  it('useIngestJobUrl invalidates the applications list for that user on success', async () => {
    let applicationsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: appsResult } = renderHook(() => useApplications('user-1'), { wrapper })
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    expect(applicationsCalls).toBe(1)

    const { result: ingestResult } = renderHook(() => useIngestJobUrl(), { wrapper })
    ingestResult.current.mutate({ user_id: 'user-1', url: 'https://boards.example.com/jobs/1' })

    await waitFor(() => expect(ingestResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(applicationsCalls).toBe(2))
  })

  it('useIngestJobUrl invalidation matches a status-filtered applications query too (prefix invalidation)', async () => {
    let applicationsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/applications`, () => {
        applicationsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: appsResult } = renderHook(() => useApplications('user-1', 'SHORTLISTED'), { wrapper })
    await waitFor(() => expect(appsResult.current.isSuccess).toBe(true))
    expect(applicationsCalls).toBe(1)

    const { result: ingestResult } = renderHook(() => useIngestJobUrl(), { wrapper })
    ingestResult.current.mutate({ user_id: 'user-1', url: 'https://boards.example.com/jobs/2' })

    await waitFor(() => expect(ingestResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(applicationsCalls).toBe(2))
  })
})
