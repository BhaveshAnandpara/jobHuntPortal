import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    globals: true,
    css: true,
    // Playwright specs (tests/e2e/**) run via `npm run e2e`, not Vitest —
    // Playwright's own `test()` is not a Vitest test and must never be
    // collected by this runner.
    exclude: ['tests/e2e/**', 'node_modules/**'],
  },
})
