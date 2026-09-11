/**
 * Route: /register — replaces the old `/welcome` no-password identity
 * creation flow. `POST /users` now requires a password and returns a
 * `LoginResponse` (token + user), so registration auto-logs-in: no
 * separate login call needed right after creating an account.
 *
 * If a token already exists (e.g. the user navigates back here directly),
 * redirect to `/` immediately rather than creating a second account —
 * `<RequireIdentity>` only guards the *other* direction (no token -> here),
 * so this page owns the reverse redirect itself.
 *
 * T4 (docs/frontend/frontend-revamp-spec.md): restyled onto the shadcn-backed
 * primitives inside the shared `AuthShell`, matching `LoginPage.tsx`. Same
 * three explicit states:
 *
 *   loading  — `createUser.isPending`: submit disabled, spinner, `aria-busy`.
 *   error    — Zod failures inline per field; a backend `400
 *              VALIDATION_ERROR` (e.g. "this email is already registered")
 *              as an inline form-level banner, with every typed value kept.
 *   success  — token persisted (auto-login) and navigate to `/`.
 *
 * Request/response handling is unchanged — `useCreateUser()` still owns the
 * call and the `timezone` empty-string-to-`null` normalization below is the
 * same wire behavior as before.
 *
 * Owner: frontend-profile-agent.
 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { Button, Input } from '../../components'
import { AuthField, AuthFormError, AuthShell } from './AuthShell'
import { useCreateUser } from '../../api/users'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import { email, optionalString, password, requiredString } from '../../utils/validation'

const registerSchema = z.object({
  email,
  display_name: requiredString,
  password,
  timezone: optionalString,
})

type RegisterFormValues = z.infer<typeof registerSchema>

export function RegisterPage() {
  const { token, setToken } = useCurrentUserId()
  const navigate = useNavigate()
  const createUser = useCreateUser()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: '', display_name: '', password: '', timezone: '' },
  })

  // Handle the "navigated back to /register with a session already set"
  // case — do this after the hooks above so hook call order stays stable
  // across renders (React's rules of hooks), but before rendering the form.
  if (token) {
    return <Navigate to="/" replace />
  }

  function onSubmit(values: RegisterFormValues) {
    setServerError(null)
    createUser.mutate(
      {
        email: values.email,
        display_name: values.display_name,
        password: values.password,
        timezone: values.timezone && values.timezone.length > 0 ? values.timezone : null,
      },
      {
        onSuccess: (response) => {
          setToken(response.access_token)
          navigate('/', { replace: true })
        },
        onError: (error) => {
          // No form reset — a rejected registration is almost always one bad
          // field (a taken email), so discarding the rest would be hostile.
          setServerError(toApiError(error).message)
        },
      },
    )
  }

  return (
    <AuthShell
      title="Create your account"
      description="Get started tracking your job search."
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand hover:underline">
            Log in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} noValidate className="mt-6 flex flex-col gap-5">
        {serverError ? (
          <AuthFormError title="Couldn't create your account" message={serverError} />
        ) : null}

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

        <AuthField id="display_name" label="Display name" error={errors.display_name?.message}>
          <Input
            id="display_name"
            autoComplete="name"
            aria-describedby={errors.display_name ? 'display_name-error' : undefined}
            invalid={Boolean(errors.display_name)}
            {...register('display_name')}
          />
        </AuthField>

        <AuthField id="password" label="Password" error={errors.password?.message}>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            aria-describedby={errors.password ? 'password-error' : undefined}
            invalid={Boolean(errors.password)}
            {...register('password')}
          />
        </AuthField>

        <AuthField id="timezone" label="Timezone" optional error={errors.timezone?.message}>
          <Input
            id="timezone"
            placeholder="e.g. America/New_York"
            aria-describedby={errors.timezone ? 'timezone-error' : undefined}
            {...register('timezone')}
          />
        </AuthField>

        <Button type="submit" variant="primary" isLoading={createUser.isPending} className="w-full">
          Create account
        </Button>
      </form>
    </AuthShell>
  )
}
