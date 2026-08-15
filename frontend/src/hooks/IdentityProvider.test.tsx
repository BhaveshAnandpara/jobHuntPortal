/**
 * Unit/component tests for the identity persistence layer — see this
 * file's header comment and `identity.ts`'s header comment for the
 * rationale (a real JWT in `localStorage`, sent as the `Authorization`
 * header on every request; `userId` is derived from it client-side for
 * display/cache-key use only). Covers: initial read from `localStorage`,
 * `setToken`/`clearToken` persisting and updating context (including the
 * derived `userId`), and `useCurrentUserId` throwing outside the provider
 * — see this repo's `docs/frontend/agent-ownership.md#frontend-shell-agent`
 * testing responsibility list.
 *
 * Owner: frontend-shell-agent.
 */

import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { IdentityProvider } from './IdentityProvider'
import { useCurrentUserId, getToken } from './identity'
import { mintTestToken } from '../../tests/support/jwt'

const STORAGE_KEY = 'jobhunt.token'

function wrapper({ children }: { children: ReactNode }) {
  return <IdentityProvider>{children}</IdentityProvider>
}

describe('IdentityProvider / useCurrentUserId', () => {
  it('throws when used outside <IdentityProvider>', () => {
    // Suppress the expected React error-boundary console noise for this
    // intentionally-failing render.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => renderHook(() => useCurrentUserId())).toThrow(
      'useCurrentUserId must be used within <IdentityProvider>',
    )
    consoleError.mockRestore()
  })

  it('starts with null when nothing is persisted', () => {
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })
    expect(result.current.token).toBeNull()
    expect(result.current.userId).toBeNull()
  })

  it('reads an already-persisted token on mount and derives userId', () => {
    const token = mintTestToken('user-existing')
    localStorage.setItem(STORAGE_KEY, token)
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })
    expect(result.current.token).toBe(token)
    expect(result.current.userId).toBe('user-existing')
  })

  it('setToken persists to localStorage and updates context', () => {
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })
    const token = mintTestToken('user-new')

    act(() => {
      result.current.setToken(token)
    })

    expect(result.current.token).toBe(token)
    expect(result.current.userId).toBe('user-new')
    expect(getToken()).toBe(token)
    expect(localStorage.getItem(STORAGE_KEY)).toBe(token)
  })

  it('clearToken removes the persisted value and resets context to null', () => {
    localStorage.setItem(STORAGE_KEY, mintTestToken('user-existing'))
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })

    act(() => {
      result.current.clearToken()
    })

    expect(result.current.token).toBeNull()
    expect(result.current.userId).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})
