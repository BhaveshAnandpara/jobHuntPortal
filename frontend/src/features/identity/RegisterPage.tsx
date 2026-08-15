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
 * Owner: frontend-profile-agent.
 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { Button, Card, FieldError, Input, PageHeader } from '../../components'
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
          setServerError(toApiError(error).message)
        },
      },
    )
  }

  return (
    <div className="mx-auto max-w-md">
      <PageHeader title="Create your account" description="Get started tracking your job search." />
      <Card>
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium text-gray-700">
              Email
            </label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              invalid={Boolean(errors.email)}
              {...register('email')}
            />
            <FieldError message={errors.email?.message} />
          </div>

          <div>
            <label htmlFor="display_name" className="mb-1 block text-sm font-medium text-gray-700">
              Display name
            </label>
            <Input
              id="display_name"
              autoComplete="name"
              invalid={Boolean(errors.display_name)}
              {...register('display_name')}
            />
            <FieldError message={errors.display_name?.message} />
          </div>

          <div>
            <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">
              Password
            </label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              invalid={Boolean(errors.password)}
              {...register('password')}
            />
            <FieldError message={errors.password?.message} />
          </div>

          <div>
            <label htmlFor="timezone" className="mb-1 block text-sm font-medium text-gray-700">
              Timezone <span className="text-gray-400">(optional)</span>
            </label>
            <Input id="timezone" placeholder="e.g. America/New_York" {...register('timezone')} />
            <FieldError message={errors.timezone?.message} />
          </div>

          {serverError ? <FieldError message={serverError} /> : null}

          <Button type="submit" variant="primary" isLoading={createUser.isPending}>
            Create account
          </Button>

          <p className="text-xs text-gray-500">
            Already have an account?{' '}
            <Link to="/login" className="font-medium text-brand hover:underline">
              Log in
            </Link>
          </p>
        </form>
      </Card>
    </div>
  )
}
