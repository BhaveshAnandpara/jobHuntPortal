/**
 * Resume Tailoring Service — functions and hooks. See
 * docs/tailored-resume/TECH.md Task 6/7.
 *
 * The download path gets most of the attention here because it is the only
 * non-JSON call in the app: it has to carry the `Authorization` header, it
 * has to read the server's `Content-Disposition` filename, and it has to
 * hand the bytes to the browser — none of which `apiClient.get` covers.
 */

import { http, HttpResponse } from 'msw'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../tests/mocks/server'
import { API_BASE_URL, ApiError } from './client'
import {
  downloadTailoredResume,
  generateTailoredResume,
  getTailoredResume,
  useGenerateTailoredResume,
  useTailoredResume,
} from './tailoredResume'
import { createWrapper } from './test-utils'
import { setToken } from '../hooks/identity'
import { mintTestToken } from '../../tests/support/jwt'

/**
 * jsdom implements neither object URLs nor a real download, so both are
 * stubbed here; `saveFileToDisk`'s contract is exactly "made an object URL
 * for these bytes and clicked an `<a download>` at it", which is what these
 * stubs let the assertions observe.
 */
function stubObjectUrls() {
  const createObjectURL = vi.fn(() => 'blob:mock-url')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
  return { createObjectURL, revokeObjectURL }
}

beforeEach(() => {
  setToken(mintTestToken('user-1'))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('tailoredResume.ts functions', () => {
  it('generateTailoredResume resolves the 201 "improved" body', async () => {
    const result = await generateTailoredResume('job-1')

    expect(result.improved).toBe(true)
    expect(result.base_score).toBe(0.62)
    expect(result.achieved_score).toBe(0.81)
    expect(result.id).toBe('tailored-1')
  })

  it('treats the 200 "no improvement" body as a success, not an error', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          {
            improved: false,
            base_score: 0.88,
            achieved_score: 0.89,
            id: null,
            message: 'Your current resume is already well-suited for this job.',
          },
          { status: 200 },
        ),
      ),
    )

    const result = await generateTailoredResume('job-1')

    expect(result.improved).toBe(false)
    expect(result.id).toBeNull()
  })

  it('surfaces the LLM-config gate as an ApiError carrying the backend code', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          {
            detail: {
              code: 'LLM_NOT_CONFIGURED',
              message: 'Add your AI provider key in Settings to use this feature.',
            },
          },
          { status: 400 },
        ),
      ),
    )

    const error = await generateTailoredResume('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('LLM_NOT_CONFIGURED')
  })

  it('getTailoredResume surfaces the "none generated yet" 404 as an ApiError', async () => {
    const error = await getTailoredResume('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('getTailoredResume resolves an existing record', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json({
          id: 'tailored-1',
          base_score: 0.62,
          achieved_score: 0.81,
          created_at: '2026-03-01T09:00:00Z',
        }),
      ),
    )

    const result = await getTailoredResume('job-1')

    expect(result.id).toBe('tailored-1')
    expect(result.achieved_score).toBe(0.81)
  })
})

describe('downloadTailoredResume', () => {
  it('sends the Bearer token and saves the file under the server-supplied filename', async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrls()
    let authorization: string | null = null
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume/download`, ({ request }) => {
        authorization = request.headers.get('Authorization')
        return HttpResponse.arrayBuffer(new ArrayBuffer(16), {
          headers: {
            'Content-Type':
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'Content-Disposition': 'attachment; filename="tailored-resume-job-1.docx"',
          },
        })
      }),
    )

    const clicked: HTMLAnchorElement[] = []
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this)
      })

    await downloadTailoredResume('job-1')

    // A `<a href>` the browser follows itself cannot carry this header —
    // which is the whole reason the bytes are fetched instead.
    expect(authorization).toBe(`Bearer ${mintTestToken('user-1')}`)
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(clicked[0].download).toBe('tailored-resume-job-1.docx')
    expect(clicked[0].href).toBe('blob:mock-url')
    // Detached again immediately — no stray anchors accumulate in the body.
    expect(document.querySelector('a[download]')).toBeNull()

    await vi.waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url'))
  })

  it('falls back to a job-derived filename when the response carries no Content-Disposition', async () => {
    stubObjectUrls()
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume/download`, () =>
        HttpResponse.arrayBuffer(new ArrayBuffer(4)),
      ),
    )
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this)
    })

    await downloadTailoredResume('job-42')

    expect(clicked[0].download).toBe('tailored-resume-job-42.docx')
  })

  it('surfaces a 404 as an ApiError and saves nothing', async () => {
    const { createObjectURL } = stubObjectUrls()
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume/download`, () =>
        HttpResponse.json(
          { detail: { code: 'NOT_FOUND', message: 'no tailored resume for this job' } },
          { status: 404 },
        ),
      ),
    )

    const error = await downloadTailoredResume('job-1').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('no tailored resume for this job')
    expect(createObjectURL).not.toHaveBeenCalled()
  })
})

describe('tailoredResume.ts hooks', () => {
  it('useTailoredResume does not fire for an empty jobId, and errors with 404 when unset', async () => {
    const { result: idle } = renderHook(() => useTailoredResume(''), { wrapper: createWrapper() })
    expect(idle.current.fetchStatus).toBe('idle')

    const { result } = renderHook(() => useTailoredResume('job-1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as ApiError).status).toBe(404)
  })

  it('useTailoredResume stays idle when disabled', () => {
    const { result } = renderHook(() => useTailoredResume('job-1', { enabled: false }), {
      wrapper: createWrapper(),
    })
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('useGenerateTailoredResume resolves and exposes the improved body', async () => {
    const { result } = renderHook(() => useGenerateTailoredResume('job-1'), {
      wrapper: createWrapper(),
    })

    result.current.mutate()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.improved).toBe(true)
  })
})
