/**
 * Skeleton-step smoke test — proves the MSW test server actually
 * intercepts a request. Not a feature test.
 */

import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from './server'

describe('MSW test server', () => {
  it('intercepts a request added at test time', async () => {
    server.use(
      http.get('http://localhost:8000/health-check', () =>
        HttpResponse.json({ ok: true }),
      ),
    )

    const response = await fetch('http://localhost:8000/health-check')
    const body = await response.json()

    expect(body).toEqual({ ok: true })
  })
})
