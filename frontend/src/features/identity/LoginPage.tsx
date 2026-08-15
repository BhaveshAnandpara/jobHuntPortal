/**
 * Route: /login — see `RegisterPage.tsx` for the account-creation
 * counterpart. Same pattern: `react-hook-form` + `zodResolver`, server
 * error surfaced via `FieldError`, `setToken` on success.
 *
 * If a token already exists, redirect to `/` immediately (same reasoning
 * as `RegisterPage.tsx`).
 *
 * Owner: frontend-profile-agent.
 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { Button, Card, FieldError, Input, PageHeader } from '../../components'
import { useLogin } from '../../api/auth'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import { email, requiredString } from '../../utils/validation'

const loginSchema = z.object({
  email,
  password: requiredString,
})

type LoginFormValues = z.infer<typeof loginSchema>

export function LoginPage() {
  const { token, setToken } = useCurrentUserId()
  const navigate = useNavigate()
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

  if (token) {
    return <Navigate to="/" replace />
  }

  function onSubmit(values: LoginFormValues) {
    setServerError(null)
    login.mutate(
      { email: values.email, password: values.password },
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
      <PageHeader title="Log in" description="Welcome back." />
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
            <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">
              Password
            </label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              invalid={Boolean(errors.password)}
              {...register('password')}
            />
            <FieldError message={errors.password?.message} />
          </div>

          {serverError ? <FieldError message={serverError} /> : null}

          <Button type="submit" variant="primary" isLoading={login.isPending}>
            Log in
          </Button>

          <p className="text-xs text-gray-500">
            Need an account?{' '}
            <Link to="/register" className="font-medium text-brand hover:underline">
              Create one
            </Link>
          </p>
        </form>
      </Card>
    </div>
  )
}
