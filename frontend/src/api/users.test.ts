/**
 * User Service — functions and hooks. See
 * docs/frontend/api-mapping.md#user-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { createUser, getPreferences, updatePreferences, useCreateUser, usePreferences, useUpdatePreferences } from './users'
import { createWrapper } from './test-utils'

describe('users.ts functions', () => {
  it('createUser posts and resolves a LoginResponse (token + user) — registration auto-logs-in', async () => {
    const result = await createUser({
      email: 'a@example.com',
      display_name: 'Ada',
      password: 'correct-password-123',
      timezone: null,
    })

    expect(result.user.email).toBe('a@example.com')
    expect(typeof result.access_token).toBe('string')
  })

  it('createUser surfaces a 400 VALIDATION_ERROR as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/users`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'email is required' } }, { status: 400 }),
      ),
    )

    const error = await createUser({
      email: '',
      display_name: '',
      password: 'correct-password-123',
      timezone: null,
    }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('VALIDATION_ERROR')
  })

  it('getPreferences surfaces a 404 when unset', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/me/preferences`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'No preferences set' } }, { status: 404 }),
      ),
    )

    const error = await getPreferences().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('updatePreferences PUTs and resolves the updated UserPreferencesResponse', async () => {
    const result = await updatePreferences({
      target_roles: ['Backend Engineer'],
      target_locations: [],
      remote_preference: null,
      excluded_companies: [],
      min_salary: null,
      salary_currency: null,
    })

    expect(result.user_id).toBe('user-1')
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/users/me/preferences`, () => HttpResponse.error()))

    const error = await getPreferences().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('users.ts hooks', () => {
  it('usePreferences resolves preferences for the authenticated user', async () => {
    const { result } = renderHook(() => usePreferences(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.user_id).toBe('user-1')
  })

  it('usePreferences surfaces a 404 as ApiError on the query result', async () => {
    server.use(
      http.get(`${API_BASE_URL}/users/me/preferences`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'No preferences set' } }, { status: 404 }),
      ),
    )

    const { result } = renderHook(() => usePreferences(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))

    expect(result.current.error).toBeInstanceOf(ApiError)
    expect((result.current.error as ApiError).status).toBe(404)
  })

  it('useCreateUser exposes the created user/token on success without touching localStorage itself', async () => {
    const { result } = renderHook(() => useCreateUser(), { wrapper: createWrapper() })

    result.current.mutate({ email: 'a@example.com', display_name: 'Ada', password: 'correct-password-123', timezone: null })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.user.email).toBe('a@example.com')
    expect(localStorage.getItem('jobhunt.token')).toBeNull()
  })

  it('useUpdatePreferences invalidates the preferences query on success, triggering a refetch', async () => {
    let getCallCount = 0
    server.use(
      http.get(`${API_BASE_URL}/users/me/preferences`, () => {
        getCallCount += 1
        return HttpResponse.json({
          id: 'pref-1',
          user_id: 'user-1',
          target_roles: ['Backend Engineer'],
          target_locations: [],
          remote_preference: null,
          excluded_companies: [],
          min_salary: null,
          salary_currency: null,
        })
      }),
    )
    const wrapper = createWrapper()
    const { result: prefsResult } = renderHook(() => usePreferences(), { wrapper })
    await waitFor(() => expect(prefsResult.current.isSuccess).toBe(true))
    expect(getCallCount).toBe(1)

    const { result: mutationResult } = renderHook(() => useUpdatePreferences(), { wrapper })
    mutationResult.current.mutate({
      target_roles: ['Backend Engineer', 'Platform Engineer'],
      target_locations: [],
      remote_preference: null,
      excluded_companies: [],
      min_salary: null,
      salary_currency: null,
    })

    await waitFor(() => expect(mutationResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(getCallCount).toBe(2))
  })
})
