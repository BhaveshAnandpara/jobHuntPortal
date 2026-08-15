/**
 * Shared Zod fragments for React Hook Form's `zodResolver`. These are
 * lightweight, client-side UX validation only — the backend's own
 * `VALIDATION_ERROR` response remains the actual authority (see
 * docs/frontend/architecture.md#thin-client-principle and
 * docs/frontend/error-handling.md's `400 VALIDATION_ERROR` row). A form
 * passing client-side validation can still be rejected by the backend;
 * every form's submit handler must surface that response's `message`
 * inline, not just trust this schema was sufficient.
 *
 * Owner: shared (first form-owning agent to need a fragment adds it here).
 */

import { z } from 'zod'

export const requiredString = z.string().trim().min(1, 'This field is required.')

export const optionalString = z.string().trim().optional()

export const httpUrl = z
  .string()
  .trim()
  .min(1, 'Enter a job posting URL.')
  .refine((value) => /^https?:\/\//i.test(value), {
    message: 'URL must start with http:// or https://.',
  })

export const email = z.string().trim().email('Enter a valid email address.')

/** Matches the backend's minimum (`UserService.create_user`/`login`,
 * `src/users/service.py`) — kept in sync by hand, same convention as
 * every other client-side rule here (a UX nicety, backend stays the real
 * authority). */
export const password = z.string().min(8, 'Password must be at least 8 characters.')
