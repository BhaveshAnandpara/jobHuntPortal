/**
 * Unit/component tests for the identity persistence layer — see this
 * file's header comment and `identity.ts`'s header comment for the
 * rationale (there is no backend session; `user_id` in `localStorage` is
 * the entire "auth" surface). Covers: initial read from `localStorage`,
 * `setUserId`/`clearUserId` persisting and updating context, and
 * `useCurrentUserId` throwing outside the provider — see this repo's
 * `docs/frontend/agent-ownership.md#frontend-shell-agent` testing
 * responsibility list.
 *
 * Owner: frontend-shell-agent.
 */

import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { IdentityProvider } from './IdentityProvider'
import { useCurrentUserId, getCurrentUserId } from './identity'

const STORAGE_KEY = 'jobhunt.userId'

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
    expect(result.current.userId).toBeNull()
  })

  it('reads an already-persisted user_id on mount', () => {
    localStorage.setItem(STORAGE_KEY, 'user-existing')
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })
    expect(result.current.userId).toBe('user-existing')
  })

  it('setUserId persists to localStorage and updates context', () => {
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })

    act(() => {
      result.current.setUserId('user-new')
    })

    expect(result.current.userId).toBe('user-new')
    expect(getCurrentUserId()).toBe('user-new')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('user-new')
  })

  it('clearUserId removes the persisted value and resets context to null', () => {
    localStorage.setItem(STORAGE_KEY, 'user-existing')
    const { result } = renderHook(() => useCurrentUserId(), { wrapper })

    act(() => {
      result.current.clearUserId()
    })

    expect(result.current.userId).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})
