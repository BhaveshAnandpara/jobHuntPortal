/**
 * `api/client.ts` owns the one HTTP boundary and the one error-normalization
 * path for the whole app — see docs/frontend/error-handling.md. These tests
 * cover every scenario the frontend-api-agent's testing responsibility
 * lists: success, a typed 4xx (400 VALIDATION_ERROR), 404, 409, 5xx, and a
 * network failure (fetch throws / never gets a response).
 */

import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError, apiClient, toApiError } from './client'

describe('apiClient', () => {
  it('resolves the parsed JSON body on a successful GET', async () => {
    server.use(
      http.get(`${API_BASE_URL}/widgets/1`, () => HttpResponse.json({ id: '1', name: 'Widget' })),
    )

    const result = await apiClient.get<{ id: string; name: string }>('/widgets/1')

    expect(result).toEqual({ id: '1', name: 'Widget' })
  })

  it('resolves undefined on a 204 No Content (e.g. DELETE)', async () => {
    server.use(http.delete(`${API_BASE_URL}/widgets/1`, () => new HttpResponse(null, { status: 204 })))

    const result = await apiClient.delete('/widgets/1')

    expect(result).toBeUndefined()
  })

  it('sends a JSON body and Content-Type header on POST', async () => {
    let receivedBody: unknown
    let receivedContentType: string | null = null
    server.use(
      http.post(`${API_BASE_URL}/widgets`, async ({ request }) => {
        receivedContentType = request.headers.get('content-type')
        receivedBody = await request.json()
        return HttpResponse.json({ id: '1' }, { status: 201 })
      }),
    )

    await apiClient.post('/widgets', { name: 'Widget' })

    expect(receivedContentType).toContain('application/json')
    expect(receivedBody).toEqual({ name: 'Widget' })
  })

  it('parses a 400 VALIDATION_ERROR into a typed ApiError', async () => {
    server.use(
      http.post(`${API_BASE_URL}/widgets`, () =>
        HttpResponse.json(
          { detail: { code: 'VALIDATION_ERROR', message: 'name is required' } },
          { status: 400 },
        ),
      ),
    )

    const error = await apiClient.post('/widgets', {}).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.status).toBe(400)
    expect(apiError.code).toBe('VALIDATION_ERROR')
    expect(apiError.message).toBe('name is required')
  })

  it('parses a 404 NOT_FOUND into a typed ApiError', async () => {
    server.use(
      http.get(`${API_BASE_URL}/widgets/missing`, () =>
        HttpResponse.json({ detail: { code: 'NOT_FOUND', message: 'Widget not found' } }, { status: 404 }),
      ),
    )

    const error = await apiClient.get('/widgets/missing').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
    expect((error as ApiError).code).toBe('NOT_FOUND')
  })

  it('parses a 409 conflict into a typed ApiError (e.g. outreach already decided)', async () => {
    server.use(
      http.post(`${API_BASE_URL}/outreach/oid-1/approve`, () =>
        HttpResponse.json(
          { detail: { code: 'CONFLICT', message: 'This was already decided' } },
          { status: 409 },
        ),
      ),
    )

    const error = await apiClient.post('/outreach/oid-1/approve', {}).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).code).toBe('CONFLICT')
    expect((error as ApiError).message).toBe('This was already decided')
  })

  it('parses a 5xx into a typed ApiError, falling back to UNKNOWN_ERROR when the body has no detail', async () => {
    server.use(http.get(`${API_BASE_URL}/widgets`, () => new HttpResponse(null, { status: 500 })))

    const error = await apiClient.get('/widgets').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).code).toBe('UNKNOWN_ERROR')
    expect((error as ApiError).message).toBe('Something went wrong. Please try again.')
  })

  it('normalizes a network failure (no response at all) into a NETWORK_ERROR ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/widgets`, () => HttpResponse.error()))

    const error = await apiClient.get('/widgets').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).code).toBe('NETWORK_ERROR')
  })

  it('never lets a raw Response or fetch rejection escape — only ApiError', async () => {
    server.use(http.get(`${API_BASE_URL}/widgets`, () => HttpResponse.error()))

    try {
      await apiClient.get('/widgets')
      expect.unreachable('expected apiClient.get to reject')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error).not.toBeInstanceOf(TypeError)
    }
  })
})

describe('toApiError', () => {
  it('passes an existing ApiError through unchanged', () => {
    const original = new ApiError(404, 'NOT_FOUND', 'Widget not found')

    expect(toApiError(original)).toBe(original)
  })

  it('normalizes a non-ApiError thrown value (e.g. a render-time TypeError) without leaking its message', () => {
    const normalized = toApiError(new TypeError('Cannot read properties of undefined'))

    expect(normalized).toBeInstanceOf(ApiError)
    expect(normalized.status).toBe(0)
    expect(normalized.code).toBe('UNKNOWN_ERROR')
    expect(normalized.message).not.toContain('Cannot read properties')
  })

  it('normalizes a thrown string/plain value the same way', () => {
    const normalized = toApiError('boom')

    expect(normalized).toBeInstanceOf(ApiError)
    expect(normalized.code).toBe('UNKNOWN_ERROR')
  })
})
