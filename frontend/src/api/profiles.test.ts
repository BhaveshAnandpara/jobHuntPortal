/**
 * Resume/Profile Service (profiles) — functions and hooks. See
 * docs/frontend/api-mapping.md#resumeprofile-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { getProfile, listProfiles, useProfile, useProfiles } from './profiles'
import { createWrapper } from './test-utils'

describe('profiles.ts functions', () => {
  it('listProfiles resolves the list for a user', async () => {
    const result = await listProfiles('user-1')
    expect(result).toHaveLength(1)
    expect(result[0].user_id).toBe('user-1')
  })

  it('getProfile resolves a single profile', async () => {
    const result = await getProfile('profile-1')
    expect(result.profile_id).toBe('profile-1')
  })

  it('getProfile surfaces a 404 as ApiError', async () => {
    server.use(
      http.get(`${API_BASE_URL}/profiles/:profileId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Profile not found' } }, { status: 404 }),
      ),
    )

    const error = await getProfile('missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.error()))

    const error = await listProfiles('user-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('profiles.ts hooks', () => {
  it('useProfiles resolves and does not fire when userId is empty', async () => {
    const { result: empty } = renderHook(() => useProfiles(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useProfiles('user-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useProfile resolves a single profile and does not fire when profileId is empty', async () => {
    const { result: empty } = renderHook(() => useProfile(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useProfile('profile-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.profile_id).toBe('profile-1')
  })

  it('useProfile surfaces a 404 as ApiError on the query result', async () => {
    server.use(
      http.get(`${API_BASE_URL}/profiles/:profileId`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Profile not found' } }, { status: 404 }),
      ),
    )

    const { result } = renderHook(() => useProfile('missing'), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as ApiError).status).toBe(404)
  })
})
