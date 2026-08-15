/**
 * Contact Discovery Service — functions and hooks. See
 * docs/frontend/api-mapping.md#contact-discovery-service.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import { listContacts, triggerContactSearch, useContacts, useTriggerContactSearch } from './contacts'
import { createWrapper } from './test-utils'

describe('contacts.ts functions', () => {
  it('listContacts resolves a ranked list', async () => {
    const result = await listContacts('job-1')
    expect(result).toHaveLength(1)
    expect(result[0].contact_type).toBe('PRACTITIONER')
  })

  it('listContacts resolves an empty list as a valid (not error) result', async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => HttpResponse.json([])))

    const result = await listContacts('job-1')

    expect(result).toEqual([])
  })

  it('triggerContactSearch posts the caller-supplied company/title/location and resolves 202', async () => {
    let receivedBody: Record<string, unknown> | undefined
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/contacts/search`, async ({ request }) => {
        receivedBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ job_id: 'job-1', requested_at: '2026-01-01T00:00:00Z' }, { status: 202 })
      }),
    )

    const result = await triggerContactSearch('job-1', {
      company: 'Acme Robotics',
      title: 'Senior Backend Engineer',
      location: 'Remote',
    })

    expect(receivedBody).toEqual({
      company: 'Acme Robotics',
      title: 'Senior Backend Engineer',
      location: 'Remote',
    })
    expect(result.job_id).toBe('job-1')
  })

  it('triggerContactSearch surfaces a 400 VALIDATION_ERROR as ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/contacts/search`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'company is required' } }, { status: 400 }),
      ),
    )

    const error = await triggerContactSearch('job-1', {
      company: '',
      title: '',
      location: null,
    }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('VALIDATION_ERROR')
  })

  it('propagates a network failure as ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => HttpResponse.error()))

    const error = await listContacts('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })
})

describe('contacts.ts hooks', () => {
  it('useContacts resolves and does not fire when jobId is empty', async () => {
    const { result: empty } = renderHook(() => useContacts(''), { wrapper: createWrapper() })
    expect(empty.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useContacts('job-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
  })

  it('useTriggerContactSearch invalidates the contacts query for that jobId on success', async () => {
    let contactsCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/contacts`, () => {
        contactsCalls += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = createWrapper()
    const { result: contactsResult } = renderHook(() => useContacts('job-1'), { wrapper })
    await waitFor(() => expect(contactsResult.current.isSuccess).toBe(true))
    expect(contactsCalls).toBe(1)

    const { result: triggerResult } = renderHook(() => useTriggerContactSearch(), { wrapper })
    triggerResult.current.mutate({
      jobId: 'job-1',
      body: { company: 'Acme Robotics', title: 'Senior Backend Engineer', location: 'Remote' },
    })

    await waitFor(() => expect(triggerResult.current.isSuccess).toBe(true))
    await waitFor(() => expect(contactsCalls).toBe(2))
  })
})
