/**
 * Route: /settings — see docs/frontend/routes.md#settings--search-preferences.
 *
 * Scoped entirely to `UserPreferences` (target_roles, target_locations,
 * remote_preference, excluded_companies, min_salary, salary_currency) —
 * there is no backend "update user identity" endpoint, so this page never
 * offers to edit email/display name (see routes.md's note on why this
 * isn't a general "profile settings" page). Nothing on this page may grow
 * beyond those six fields: they are the entire `UpdateUserPreferencesRequest`.
 *
 * `PUT /users/me/preferences` is a full replace, so `onSubmit` always
 * submits the complete current form state, never a diff. A 404 from
 * `usePreferences` means "unset" (valid, not an error) and renders an
 * empty/default form instead of an error state.
 *
 * T10 (docs/frontend/frontend-revamp-spec.md): restyled onto the shadcn-backed
 * primitives in src/components. The full-replace contract drives the layout —
 * every field lives inside one `<form>` with a single trailing save bar, and
 * there is deliberately no per-field save/auto-save affordance that would
 * imply a partial update. "Discard changes" resets to the last loaded server
 * state, which reinforces the same "this whole page is one document" model.
 *
 * Owner: frontend-profile-agent.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { Button, Card, ErrorState, FieldError, Input, PageHeader, Select, Skeleton } from '../../components'
import { usePreferences, useUpdatePreferences } from '../../api/users'
import { toApiError } from '../../api/client'
import { formatDateTime } from '../../utils/format'
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

const REMOTE_PREFERENCE_VALUES = new Set(REMOTE_PREFERENCE_OPTIONS.map((option) => option.value))

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

/** "2 roles" / "1 role" — a live read-out of how many comma-separated entries
 * the field will actually submit, since commas are load-bearing here. */
function countLabel(value: string, singular: string, plural: string): string | null {
  const count = parseCsv(value).length
  if (count === 0) {
    return null
  }
  return `${count} ${count === 1 ? singular : plural}`
}

function Section({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <Card className="p-0">
      <fieldset className="flex flex-col">
        <legend className="sr-only">{title}</legend>
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          <p className="mt-0.5 text-xs text-gray-500">{description}</p>
        </div>
        <div className="flex flex-col gap-5 px-6 py-5">{children}</div>
      </fieldset>
    </Card>
  )
}

/**
 * One labelled form row. The control is a render prop so the field owns the
 * `id`/`aria-describedby`/`invalid` wiring instead of every call site
 * re-deriving the same three ids by hand.
 */
function Field({
  id,
  label,
  hint,
  optional = false,
  badge,
  error,
  children,
}: {
  id: string
  label: string
  hint?: string
  optional?: boolean
  badge?: string | null
  error?: string
  children: (control: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode
}) {
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium text-gray-900">
          {label}
          {optional ? <span className="ml-1 font-normal text-gray-400">(optional)</span> : null}
        </label>
        {badge ? <span className="shrink-0 text-xs text-gray-500">{badge}</span> : null}
      </div>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint ? (
        <p id={hintId} className="text-xs text-gray-500">
          {hint}
        </p>
      ) : null}
      {/* `FieldError` is the app-wide inline validation convention (see
          error-handling.md); it's wrapped here only to give the message an id
          the control can point at via `aria-describedby`. */}
      {error ? (
        <div id={errorId}>
          <FieldError message={error} />
        </div>
      ) : null}
    </div>
  )
}

function PreferencesFormSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {[3, 2, 1].map((fieldCount, sectionIndex) => (
        <Card key={sectionIndex} className="p-0">
          <div className="border-b border-gray-200 px-6 py-4">
            <Skeleton className="h-4 w-40" />
          </div>
          <div className="flex flex-col gap-5 px-6 py-5">
            {Array.from({ length: fieldCount }, (_, index) => (
              <div key={index} className="flex flex-col gap-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
          </div>
        </Card>
      ))}
      <Skeleton className="h-16 w-full" />
    </div>
  )
}

export function SettingsPage() {
  const preferencesQuery = usePreferences()
  const updatePreferences = useUpdatePreferences()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    control,
    register,
    handleSubmit,
    reset,
    watch,
    formState: { errors, isDirty },
  } = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsFormSchema),
    defaultValues: toFormValues(undefined),
  })

  const queryError = preferencesQuery.error ? toApiError(preferencesQuery.error) : null
  const isUnset = queryError?.status === 404
  const pageError = queryError && !isUnset ? queryError : null
  const lastSavedAt = preferencesQuery.data?.updated_at

  useEffect(() => {
    if (preferencesQuery.isSuccess) {
      reset(toFormValues(preferencesQuery.data))
    } else if (preferencesQuery.isError && isUnset) {
      reset(toFormValues(undefined))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferencesQuery.isSuccess, preferencesQuery.isError, preferencesQuery.data, isUnset])

  function onSubmit(values: SettingsFormValues) {
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
      body,
      {
        onSuccess: (saved) => {
          // The response is the authoritative post-replace state — re-seeding
          // the form from it clears the dirty flag and makes what's on screen
          // exactly what the server now holds.
          reset(toFormValues(saved))
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
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Search Preferences"
        description="These preferences help the platform evaluate opportunities on your behalf."
      />

      {preferencesQuery.isLoading ? (
        <PreferencesFormSkeleton />
      ) : pageError ? (
        <ErrorState message={pageError.message} onRetry={() => void preferencesQuery.refetch()} />
      ) : (
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
          {isUnset ? (
            <p
              role="status"
              className="rounded-lg border border-gray-200 bg-status-progress-bg px-4 py-3 text-sm text-status-progress"
            >
              You haven&apos;t set any search preferences yet. Fill in what you know and save — you can change
              any of it later.
            </p>
          ) : lastSavedAt ? (
            <p className="text-xs text-gray-500">Last saved {formatDateTime(lastSavedAt)}</p>
          ) : null}

          <Section
            title="What you're looking for"
            description="Used to decide whether an incoming job posting is worth evaluating for you."
          >
            <Field
              id="targetRoles"
              label="Target roles"
              hint="Separate multiple roles with commas."
              badge={countLabel(watch('targetRoles'), 'role', 'roles')}
              error={errors.targetRoles?.message}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  placeholder="e.g. Backend Engineer, Platform Engineer"
                  {...register('targetRoles')}
                />
              )}
            </Field>

            <Field
              id="targetLocations"
              label="Target locations"
              hint="Separate multiple locations with commas."
              badge={countLabel(watch('targetLocations'), 'location', 'locations')}
              error={errors.targetLocations?.message}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  placeholder="e.g. Remote, New York, Berlin"
                  {...register('targetLocations')}
                />
              )}
            </Field>

            <Field
              id="remotePreference"
              label="Remote work preference"
              hint="Leave as “Not set” to keep this out of the evaluation entirely."
              error={errors.remotePreference?.message}
            >
              {({ id, invalid }) => (
                <Controller
                  control={control}
                  name="remotePreference"
                  render={({ field }) => (
                    <Select
                      id={id}
                      options={REMOTE_PREFERENCE_OPTIONS}
                      value={field.value}
                      // The hidden native `<select>` Radix renders for form
                      // integration has no options while the dropdown is
                      // closed, so it can emit a spurious `''` change. Taking
                      // it would both mark the form dirty on load and PUT
                      // `remote_preference: ""` — not a `RemoteWorkPreference`.
                      // Only the five real option values are accepted.
                      onValueChange={(next) => {
                        if (REMOTE_PREFERENCE_VALUES.has(next)) {
                          field.onChange(next)
                        }
                      }}
                      invalid={invalid}
                      aria-label="Remote work preference"
                    />
                  )}
                />
              )}
            </Field>
          </Section>

          <Section
            title="Compensation"
            description="Both fields are optional — leave them blank to set no salary floor."
          >
            <div className="grid gap-5 sm:grid-cols-[2fr_1fr]">
              <Field
                id="minSalary"
                label="Minimum salary"
                optional
                error={errors.minSalary?.message}
              >
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    inputMode="decimal"
                    aria-describedby={describedBy}
                    invalid={invalid}
                    placeholder="e.g. 120000"
                    {...register('minSalary')}
                  />
                )}
              </Field>

              <Field
                id="salaryCurrency"
                label="Currency"
                optional
                error={errors.salaryCurrency?.message}
              >
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    placeholder="e.g. USD"
                    {...register('salaryCurrency')}
                  />
                )}
              </Field>
            </div>
          </Section>

          <Section
            title="Exclusions"
            description="Opportunities from these companies are excluded before they reach your pipeline."
          >
            <Field
              id="excludedCompanies"
              label="Excluded companies"
              hint="Separate multiple companies with commas."
              badge={countLabel(watch('excludedCompanies'), 'company', 'companies')}
              error={errors.excludedCompanies?.message}
            >
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  placeholder="e.g. Acme Corp, Globex"
                  {...register('excludedCompanies')}
                />
              )}
            </Field>
          </Section>

          {serverError ? (
            <p
              role="alert"
              className="rounded-lg border border-gray-200 bg-status-negative-bg px-4 py-3 text-sm text-status-negative"
            >
              {serverError}
            </p>
          ) : null}

          <div className="sticky bottom-0 flex flex-col gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-gray-500">
              Saving replaces every preference on this page at once — there are no partial saves.
            </p>
            <div className="flex shrink-0 items-center gap-3">
              {isDirty ? <span className="text-xs text-status-attention">Unsaved changes</span> : null}
              <Button
                type="button"
                variant="secondary"
                disabled={!isDirty || updatePreferences.isPending}
                onClick={() => {
                  setServerError(null)
                  reset()
                }}
              >
                Discard changes
              </Button>
              <Button type="submit" variant="primary" isLoading={updatePreferences.isPending}>
                Save preferences
              </Button>
            </div>
          </div>
        </form>
      )}
    </div>
  )
}
