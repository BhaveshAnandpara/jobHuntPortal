/**
 * Route: /welcome — see docs/frontend/routes.md#welcome--onboarding and
 * docs/frontend/user-flows.md#first-visit-flow.
 *
 * Creates the local `user_id` every subsequent API call needs. This is
 * explicitly NOT authentication — there is no login, no password, no
 * session token, just a locally-persisted identifier (see
 * src/hooks/identity.ts's own header comment). Copy on this page must
 * never imply otherwise.
 *
 * If an identity already exists (e.g. the user navigates back here
 * directly), redirect to `/` immediately rather than creating a second
 * user — `<RequireIdentity>` only guards the *other* direction (no
 * identity -> here), so this page owns the reverse redirect itself.
 *
 * Owner: frontend-profile-agent.
 */

import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Navigate, useNavigate } from 'react-router-dom'
import { Button, Card, FieldError, Input, PageHeader } from '../../components'
import { useCreateUser } from '../../api/users'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import { email, optionalString, requiredString } from '../../utils/validation'

const identitySchema = z.object({
  email,
  display_name: requiredString,
  timezone: optionalString,
})

type IdentityFormValues = z.infer<typeof identitySchema>

export function WelcomePage() {
  const { userId, setUserId } = useCurrentUserId()
  const navigate = useNavigate()
  const createUser = useCreateUser()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<IdentityFormValues>({
    resolver: zodResolver(identitySchema),
    defaultValues: { email: '', display_name: '', timezone: '' },
  })

  // Handle the "navigated back to /welcome with an identity already set"
  // case — do this after the hooks above so hook call order stays stable
  // across renders (React's rules of hooks), but before rendering the form.
  if (userId) {
    return <Navigate to="/" replace />
  }

  function onSubmit(values: IdentityFormValues) {
    setServerError(null)
    createUser.mutate(
      {
        email: values.email,
        display_name: values.display_name,
        timezone: values.timezone && values.timezone.length > 0 ? values.timezone : null,
      },
      {
        onSuccess: (user) => {
          setUserId(user.id)
          navigate('/', { replace: true })
        },
        onError: (error) => {
          const apiError = toApiError(error)
          if (apiError.code === 'VALIDATION_ERROR') {
            setServerError(apiError.message)
          } else {
            setServerError(apiError.message)
          }
        },
      },
    )
  }

  return (
    <div className="mx-auto max-w-md">
      <PageHeader title="Welcome" description="Create your identity to get started." />
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
            <label htmlFor="timezone" className="mb-1 block text-sm font-medium text-gray-700">
              Timezone <span className="text-gray-400">(optional)</span>
            </label>
            <Input id="timezone" placeholder="e.g. America/New_York" {...register('timezone')} />
            <FieldError message={errors.timezone?.message} />
          </div>

          {serverError ? <FieldError message={serverError} /> : null}

          <Button type="submit" variant="primary" isLoading={createUser.isPending}>
            Create identity
          </Button>

          <p className="text-xs text-gray-500">
            This creates a local identifier stored in your browser so the app can keep track of your
            resumes, preferences, and opportunities on this device. There is no password and no login
            step — nothing here is a real account or session.
          </p>
        </form>
      </Card>
    </div>
  )
}
