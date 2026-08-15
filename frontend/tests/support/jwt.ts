/**
 * Test-only JWT construction — for Vitest unit/component tests, which hit
 * MSW-mocked endpoints that never verify a signature. A real signed token
 * isn't needed here (unlike the Playwright e2e suite, which drives a real
 * backend and needs a real, deterministic `JWT_SECRET_KEY`) — only a
 * correctly-shaped one, so `hooks/identity.ts`'s `decodeUserId` can read
 * the `sub` claim back out for display/cache-key purposes.
 */

function base64url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Builds a fake-but-correctly-shaped JWT encoding `userId` as `sub`. The
 * "signature" segment is a static placeholder — never verified in tests. */
export function mintTestToken(userId: string): string {
  const header = base64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = base64url(JSON.stringify({ sub: userId, iat: 0, exp: 9999999999 }))
  return `${header}.${payload}.test-signature`
}
