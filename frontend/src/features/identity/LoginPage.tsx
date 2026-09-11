/**
 * Route: /login — see `RegisterPage.tsx` for the account-creation
 * counterpart. Same pattern: `react-hook-form` + `zodResolver`, server
 * error surfaced inline, `setToken` on success.
 *
 * If a token already exists, redirect to `/` immediately (same reasoning
 * as `RegisterPage.tsx`).
 *
 * T4 (docs/frontend/frontend-revamp-spec.md): restyled onto the shadcn-backed
 * primitives (`components/Button`, `components/Input`) inside the shared
 * `AuthShell`. The three states this screen must cover explicitly:
 *
 *   loading  — `login.isPending`: submit button is disabled, shows a spinner
 *              and `aria-busy`, and the stale server error is cleared so the
 *              user isn't reading a failure from the previous attempt.
 *   error    — client-side Zod failures render per field via `AuthField`;
 *              a `401 UNAUTHORIZED` from `POST /auth/login` renders as an
 *              inline form-level banner (NOT a toast) and the form keeps
 *              every value the user typed.
 *   success  — token persisted, navigate to `/` (replace, so Back doesn't
 *              land on a login form the user is already past).
 *
 * The request/response handling itself is unchanged: `useLogin()` still owns
 * the call, `toApiError` still normalizes the failure, and the login 401 is
 * still exempted from the global auto-logout redirect in `api/client.ts`.
 *
 * Owner: frontend-profile-agent.
 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Button, Input } from '../../components'
import { AuthField, AuthFormError, AuthShell } from './AuthShell'
import { useLogin } from '../../api/auth'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import { email, requiredString } from '../../utils/validation'

const loginSchema = z.object({
  email,
  password: requiredString,
})

type LoginFormValues = z.infer<typeof loginSchema>

/**
 * Where to land after a successful sign-in. `app/RequireIdentity` carries the
 * protected route the user was bounced off in the navigation state as `from`
 * (T3) — honoring it means an expired session drops the user back where they
 * were instead of always on the dashboard.
 *
 * Only a same-origin, root-relative path is accepted: anything else (an
 * absolute URL, a protocol-relative `//host` path, or a non-string that got
 * into history state) falls back to `/` rather than becoming an open redirect.
 */
function resolveRedirectTarget(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from
  if (typeof from !== 'string' || !from.startsWith('/') || from.startsWith('//')) {
    return '/'
  }
  // Never bounce back to an auth route — that would re-render this form.
  if (from === '/login' || from === '/register') {
    return '/'
  }
  return from
}

export function LoginPage() {
  const { token, setToken } = useCurrentUserId()
  const navigate = useNavigate()
  const location = useLocation()
  const redirectTo = resolveRedirectTarget(location.state)
  const login = useLogin()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  })

  // Already authenticated (a returning visit to /login, or the re-render that
  // follows `setToken` below). Same destination as a fresh sign-in, so the
  // two paths can't disagree about where the user ends up.
  if (token) {
    return <Navigate to={redirectTo} replace />
  }

  function onSubmit(values: LoginFormValues) {
    setServerError(null)
    login.mutate(
      { email: values.email, password: values.password },
      {
        onSuccess: (response) => {
          setToken(response.access_token)
          navigate(redirectTo, { replace: true })
        },
        onError: (error) => {
          // No form reset here, deliberately: the user's email is almost
          // always correct and only the password needs fixing.
          setServerError(toApiError(error).message)
        },
      },
    )
  }

  return (
    <AuthShell
      title="Log in"
      description="Sign in to pick your job search back up."
      footer={
        <>
          Need an account?{' '}
          <Link to="/register" className="font-medium text-brand hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} noValidate className="mt-6 flex flex-col gap-5">
        {serverError ? <AuthFormError title="Couldn't log you in" message={serverError} /> : null}

        <AuthField id="email" label="Email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            aria-describedby={errors.email ? 'email-error' : undefined}
            invalid={Boolean(errors.email)}
            {...register('email')}
          />
        </AuthField>

        <AuthField id="password" label="Password" error={errors.password?.message}>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            aria-describedby={errors.password ? 'password-error' : undefined}
            invalid={Boolean(errors.password)}
            {...register('password')}
          />
        </AuthField>

        <Button type="submit" variant="primary" isLoading={login.isPending} className="w-full">
          Log in
        </Button>
      </form>
    </AuthShell>
  )
}
