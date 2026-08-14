/**
 * Vitest global setup — see vite.config.ts's `test.setupFiles`. Owner:
 * shared (whichever agent first needs a new global test convention adds
 * it here, per docs/frontend/testing-strategy.md).
 */

import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/server'

// jsdom implements neither the Pointer Events capture methods nor
// scrollIntoView. Radix UI's Select (and Dialog, if it ever needs the
// same) call these during pointer-driven open/close/scroll handling —
// without a no-op polyfill, interacting with those components in a test
// throws `target.hasPointerCapture is not a function`. Added here (not in
// an individual test) because it's a jsdom environment gap, not something
// specific to one component's test — any future Radix-driven interaction
// test needs the same polyfill.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  localStorage.clear()
})
afterAll(() => server.close())
