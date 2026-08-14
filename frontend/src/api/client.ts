/**
 * Shared fetch wrapper and error normalization boundary. Every function in
 * `src/api/*.ts` calls through here — no feature module ever calls `fetch`
 * directly. See docs/frontend/error-handling.md and
 * docs/frontend/api-mapping.md#error-contract-applies-to-every-table-above.
 *
 * Owner: frontend-api-agent.
 */

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

/**
 * The one error shape every backend router returns for a non-2xx response
 * (verified across all eight routers — see api-mapping.md's error contract
 * note): `{"detail": {"code": ErrorCode, "message": str}}`. `status` is not
 * part of that body; it's the HTTP status code, attached here so callers
 * never need to inspect a raw `Response` themselves.
 *
 * `code === "NETWORK_ERROR"` is a frontend-only sentinel (not a backend
 * `ErrorCode` value) for the case where no response was received at all —
 * see docs/frontend/error-handling.md's "network failure" row.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type BackendErrorBody = {
  detail?: {
    code?: string
    message?: string
  }
}

async function parseErrorBody(response: Response): Promise<BackendErrorBody | null> {
  try {
    return (await response.json()) as BackendErrorBody
  } catch {
    return null
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', "Can't reach the server. Check your connection.")
  }

  if (!response.ok) {
    const body = await parseErrorBody(response)
    throw new ApiError(
      response.status,
      body?.detail?.code ?? 'UNKNOWN_ERROR',
      body?.detail?.message ?? 'Something went wrong. Please try again.',
    )
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

/**
 * The one HTTP call surface for the whole app. `path` is relative to
 * `API_BASE_URL` and must include a leading `/` (e.g. `/users`) — no route
 * prefix exists in the deployed backend today, see api-mapping.md's header
 * note.
 */
/**
 * Normalizes any thrown value into an `ApiError` — used by call sites
 * (e.g. a top-level mutation `onError`, or the route-level error boundary
 * in `app/ErrorBoundary.tsx`) that need one consistent shape regardless of
 * whether the failure came from this client, a JS runtime exception, or
 * something else unexpected. Never surfaces the original error's raw
 * message for a non-`ApiError` input — see
 * docs/frontend/error-handling.md's "what never happens" section.
 */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError(0, 'UNKNOWN_ERROR', 'Something went wrong. Please try again.')
}

export const apiClient = {
  get: <T>(path: string): Promise<T> => request<T>(path, { method: 'GET' }),

  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  put: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: 'PUT',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  patch: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  delete: (path: string): Promise<void> => request<void>(path, { method: 'DELETE' }),
}
