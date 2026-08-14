/**
 * Route: /settings — see docs/frontend/routes.md#settings--search-preferences.
 *
 * Scoped entirely to `UserPreferences` (target_roles, target_locations,
 * remote_preference, excluded_companies, min_salary, salary_currency) —
 * there is no backend "update user identity" endpoint, so this page never
 * offers to edit email/display name (see routes.md's note on why this
 * isn't a general "profile settings" page).
 *
 * `PUT /users/{id}/preferences` is a full replace, so `onSubmit` always
 * submits the complete current form state, never a diff. A 404 from
 * `usePreferences` means "unset" (valid, not an error) and renders an
 * empty/default form instead of an error state.
 *
 * Owner: frontend-profile-agent.
 */

import { useEffect, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { Button, Card, ErrorState, FieldError, Input, PageHeader, Select, Skeleton } from '../../components'
import { usePreferences, useUpdatePreferences } from '../../api/users'
import { toApiError } from '../../api/client'
import { useCurrentUserId } from '../../hooks/identity'
import type {
  RemoteWorkPreference,
  UpdateUserPreferencesRequest,
  UserPreferencesResponse,
} from '../../api/types'

/** Sentinel form value for "no remote preference set" (`null` on the wire),
 * distinct from the real `NO_PREFERENCE` enum value — Radix `Select`
 * doesn't accept an empty-string value, so a non-empty sentinel is used
 * and mapped back to `null` at submit time. */
const UNSET_REMOTE_PREFERENCE = '__UNSET__'

const REMOTE_PREFERENCE_OPTIONS = [
  { value: UNSET_REMOTE_PREFERENCE, label: 'Not set' },
  { value: 'REMOTE', label: 'Remote' },
  { value: 'HYBRID', label: 'Hybrid' },
  { value: 'ONSITE', label: 'Onsite' },
  { value: 'NO_PREFERENCE', label: 'No preference' },
]

const settingsFormSchema = z.object({
  targetRoles: z.string(),
  targetLocations: z.string(),
  remotePreference: z.string(),
  excludedCompanies: z.string(),
  minSalary: z.string().refine((value) => value.trim() === '' || (!Number.isNaN(Number(value)) && Number(value) >= 0), {
    message: 'Enter a valid non-negative number.',
  }),
  salaryCurrency: z.string(),
})

type SettingsFormValues = z.infer<typeof settingsFormSchema>

function toFormValues(preferences: UserPreferencesResponse | undefined): SettingsFormValues {
  return {
    targetRoles: preferences?.target_roles?.join(', ') ?? '',
    targetLocations: preferences?.target_locations?.join(', ') ?? '',
    remotePreference: preferences?.remote_preference ?? UNSET_REMOTE_PREFERENCE,
    excludedCompanies: preferences?.excluded_companies?.join(', ') ?? '',
    minSalary: preferences?.min_salary != null ? String(preferences.min_salary) : '',
    salaryCurrency: preferences?.salary_currency ?? '',
  }
}

function parseCsv(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
}

function PreferencesFormSkeleton() {
  return (
    <Card>
      <div className="flex flex-col gap-5">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-9 w-full" />
          </div>
        ))}
        <Skeleton className="h-9 w-32" />
      </div>
    </Card>
  )
}

export function SettingsPage() {
  const { userId } = useCurrentUserId()
  const activeUserId = userId ?? ''
  const preferencesQuery = usePreferences(activeUserId)
  const updatePreferences = useUpdatePreferences()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    control,
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsFormSchema),
    defaultValues: toFormValues(undefined),
  })

  const queryError = preferencesQuery.error ? toApiError(preferencesQuery.error) : null
  const isUnset = queryError?.status === 404
  const pageError = queryError && !isUnset ? queryError : null

  useEffect(() => {
    if (preferencesQuery.isSuccess) {
      reset(toFormValues(preferencesQuery.data))
    } else if (preferencesQuery.isError && isUnset) {
      reset(toFormValues(undefined))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferencesQuery.isSuccess, preferencesQuery.isError, preferencesQuery.data, isUnset])

  function onSubmit(values: SettingsFormValues) {
    if (!userId) return
    setServerError(null)

    const body: UpdateUserPreferencesRequest = {
      target_roles: parseCsv(values.targetRoles),
      target_locations: parseCsv(values.targetLocations),
      remote_preference:
        values.remotePreference === UNSET_REMOTE_PREFERENCE
          ? null
          : (values.remotePreference as RemoteWorkPreference),
      excluded_companies: parseCsv(values.excludedCompanies),
      min_salary: values.minSalary.trim() === '' ? null : Number(values.minSalary),
      salary_currency: values.salaryCurrency.trim() === '' ? null : values.salaryCurrency.trim(),
    }

    updatePreferences.mutate(
      { userId, body },
      {
        onSuccess: () => {
          toast.success('Preferences saved.')
        },
        onError: (error) => {
          const apiError = toApiError(error)
          if (apiError.code === 'VALIDATION_ERROR') {
            setServerError(apiError.message)
          } else {
            toast.error(apiError.message)
          }
        },
      },
    )
  }

  return (
    <div>
      <PageHeader
        title="Search Preferences"
        description="These preferences help the platform evaluate opportunities on your behalf."
      />

      {preferencesQuery.isLoading ? (
        <PreferencesFormSkeleton />
      ) : pageError ? (
        <ErrorState message={pageError.message} onRetry={() => void preferencesQuery.refetch()} />
      ) : (
        <Card>
          <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-5">
            <div>
              <label htmlFor="targetRoles" className="mb-1 block text-sm font-medium text-gray-700">
                Target roles
              </label>
              <Input
                id="targetRoles"
                placeholder="e.g. Backend Engineer, Platform Engineer"
                {...register('targetRoles')}
              />
              <p className="mt-1 text-xs text-gray-500">Separate multiple roles with commas.</p>
              <FieldError message={errors.targetRoles?.message} />
            </div>

            <div>
              <label htmlFor="targetLocations" className="mb-1 block text-sm font-medium text-gray-700">
                Target locations
              </label>
              <Input
                id="targetLocations"
                placeholder="e.g. Remote, New York, Berlin"
                {...register('targetLocations')}
              />
              <p className="mt-1 text-xs text-gray-500">Separate multiple locations with commas.</p>
              <FieldError message={errors.targetLocations?.message} />
            </div>

            <div>
              <label htmlFor="remotePreference" className="mb-1 block text-sm font-medium text-gray-700">
                Remote work preference
              </label>
              <Controller
                control={control}
                name="remotePreference"
                render={({ field }) => (
                  <Select
                    id="remotePreference"
                    options={REMOTE_PREFERENCE_OPTIONS}
                    value={field.value}
                    onValueChange={field.onChange}
                    aria-label="Remote work preference"
                  />
                )}
              />
              <FieldError message={errors.remotePreference?.message} />
            </div>

            <div>
              <label htmlFor="excludedCompanies" className="mb-1 block text-sm font-medium text-gray-700">
                Excluded companies
              </label>
              <Input
                id="excludedCompanies"
                placeholder="e.g. Acme Corp, Globex"
                {...register('excludedCompanies')}
              />
              <p className="mt-1 text-xs text-gray-500">
                Opportunities from these companies will be excluded. Separate with commas.
              </p>
              <FieldError message={errors.excludedCompanies?.message} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="minSalary" className="mb-1 block text-sm font-medium text-gray-700">
                  Minimum salary <span className="text-gray-400">(optional)</span>
                </label>
                <Input
                  id="minSalary"
                  inputMode="decimal"
                  placeholder="e.g. 120000"
                  invalid={Boolean(errors.minSalary)}
                  {...register('minSalary')}
                />
                <FieldError message={errors.minSalary?.message} />
              </div>
              <div>
                <label htmlFor="salaryCurrency" className="mb-1 block text-sm font-medium text-gray-700">
                  Currency <span className="text-gray-400">(optional)</span>
                </label>
                <Input id="salaryCurrency" placeholder="e.g. USD" {...register('salaryCurrency')} />
                <FieldError message={errors.salaryCurrency?.message} />
              </div>
            </div>

            {serverError ? <FieldError message={serverError} /> : null}

            <div>
              <Button type="submit" variant="primary" isLoading={updatePreferences.isPending}>
                Save preferences
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  )
}
