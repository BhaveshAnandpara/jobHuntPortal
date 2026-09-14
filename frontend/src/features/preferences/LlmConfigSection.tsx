/**
 * The "AI provider" section of `/settings` — see
 * docs/llm-provider-config/PRODUCT.md.
 *
 * A sibling form, not more rows in the preferences form: `SettingsPage`'s
 * own contract is exactly the six `UpdateUserPreferencesRequest` fields, and
 * this is a different endpoint (`PUT /users/me/llm-config`) with different
 * semantics — its save makes a live call to the chosen provider before
 * anything is stored, so it cannot share a submit handler with a save that
 * only writes rows. Its own zod schema, its own `useForm`, its own button.
 *
 * Write-only key handling: the API key input is never seeded from the server
 * (the server has no plaintext key to give — see
 * `users/repository.py::_to_llm_config`). The only echo of a saved key is
 * `key_hint`, a masked tail like `...ab12`, rendered as read-only context
 * beside the label so the field itself is always genuinely empty and a
 * submit can never resend a value the user didn't type.
 *
 * `PUT` is a full replace and re-validates on every save, so the key is
 * required each time — including when only the model changed. That is the
 * backend contract, and the field's hint says so rather than the UI
 * pretending otherwise.
 *
 * Owner: frontend-profile-agent.
 */

import { useEffect, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { Button, Card, Input, Select, Skeleton } from '../../components'
import { Field, Section } from './SettingsSection'
import { useLlmConfig, useUpdateLlmConfig } from '../../api/llmConfig'
import { toApiError } from '../../api/client'
import { formatDateTime } from '../../utils/format'
import type { LlmProviderName, UpdateUserLlmConfigRequest, UserLlmConfigResponse } from '../../api/types'

const PROVIDER_OPTIONS = [
  { value: 'claude', label: 'Claude (Anthropic)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'groq', label: 'Groq' },
  { value: 'gemini', label: 'Google Gemini' },
]

const PROVIDER_VALUES = new Set(PROVIDER_OPTIONS.map((option) => option.value))

/** Nothing is preselected for a user who has never configured a provider —
 * picking one for them would make "whichever key you already have" a silent
 * default. An empty string is the unselected form value; it fails the schema
 * below, and is mapped to `undefined` for Radix (which has no empty-string
 * value) so the trigger shows its placeholder. */
const UNSELECTED_PROVIDER = ''

const llmConfigFormSchema = z.object({
  provider: z.string().refine((value) => PROVIDER_VALUES.has(value), {
    message: 'Choose an AI provider.',
  }),
  model: z.string(),
  apiKey: z.string().refine((value) => value.trim().length > 0, {
    message: 'Enter your API key — saving re-checks it with the provider.',
  }),
})

type LlmConfigFormValues = z.infer<typeof llmConfigFormSchema>

/** `apiKey` is always blank: a saved key is unreadable to this app by
 * design, so there is nothing to seed it with on load, and clearing it on
 * every (re)seed is what makes a successful save leave an empty field. */
function toFormValues(config: UserLlmConfigResponse | undefined): LlmConfigFormValues {
  return {
    provider: config?.provider ?? UNSELECTED_PROVIDER,
    model: config?.model ?? '',
    apiKey: '',
  }
}

function LlmConfigSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Card className="p-0">
        <div className="border-b border-gray-200 px-6 py-4">
          <Skeleton className="h-4 w-32" />
        </div>
        <div className="flex flex-col gap-5 px-6 py-5">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex flex-col gap-2">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-9 w-full" />
            </div>
          ))}
        </div>
      </Card>
      <Skeleton className="h-16 w-full" />
    </div>
  )
}

export function LlmConfigSection() {
  const llmConfigQuery = useLlmConfig()
  const updateLlmConfig = useUpdateLlmConfig()
  const [serverError, setServerError] = useState<string | null>(null)

  const {
    control,
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<LlmConfigFormValues>({
    resolver: zodResolver(llmConfigFormSchema),
    defaultValues: toFormValues(undefined),
  })

  const queryError = llmConfigQuery.error ? toApiError(llmConfigQuery.error) : null
  /** 404 is "no provider configured yet" — the same "unset is not an error"
   * convention `GET /users/me/preferences` uses, so it renders an empty form
   * rather than a failure. */
  const isUnset = queryError?.status === 404
  const loadError = queryError && !isUnset ? queryError : null

  /** The `PUT` response is the freshest masked view of what's stored, and it
   * arrives before the invalidated `GET` has refetched — preferring it is
   * what makes the key hint flip to the just-saved key's tail immediately
   * after a save. */
  const savedConfig: UserLlmConfigResponse | undefined =
    updateLlmConfig.data ?? (llmConfigQuery.isSuccess ? llmConfigQuery.data : undefined)

  useEffect(() => {
    if (llmConfigQuery.isSuccess) {
      reset(toFormValues(llmConfigQuery.data))
    } else if (llmConfigQuery.isError && isUnset) {
      reset(toFormValues(undefined))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmConfigQuery.isSuccess, llmConfigQuery.isError, llmConfigQuery.data, isUnset])

  function onSubmit(values: LlmConfigFormValues) {
    setServerError(null)

    const body: UpdateUserLlmConfigRequest = {
      provider: values.provider as LlmProviderName,
      model: values.model.trim() === '' ? null : values.model.trim(),
      api_key: values.apiKey.trim(),
    }

    updateLlmConfig.mutate(body, {
      onSuccess: (saved) => {
        // Re-seed from the saved (masked) state: provider/model come back
        // from the server, and `toFormValues` always blanks the key field,
        // so the plaintext key leaves the DOM the moment it is accepted.
        reset(toFormValues(saved))
        toast.success('AI provider saved.')
      },
      onError: (error) => {
        const apiError = toApiError(error)
        // A key the provider rejected comes back as 400 VALIDATION_ERROR
        // with a provider-specific message ("This key was rejected by
        // Groq…") — that belongs inline next to the form that caused it,
        // not in a toast that disappears.
        if (apiError.code === 'VALIDATION_ERROR') {
          setServerError(apiError.message)
        } else {
          toast.error(apiError.message)
        }
      },
    })
  }

  if (llmConfigQuery.isLoading) {
    return <LlmConfigSkeleton />
  }

  const keyHint = savedConfig?.key_hint
  const lastValidatedAt = savedConfig?.last_validated_at

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
      <Section
        title="AI provider"
        description="JobHunt runs every AI-powered step — resume parsing, matching, outreach drafts — on your own provider key."
      >
        {isUnset && !savedConfig ? (
          <p className="rounded-lg border border-gray-200 bg-status-progress-bg px-4 py-3 text-sm text-status-progress">
            You haven&apos;t added an AI provider key yet. AI-powered features stay locked until you do.
          </p>
        ) : lastValidatedAt ? (
          <p className="text-xs text-gray-500">Key last verified {formatDateTime(lastValidatedAt)}</p>
        ) : null}

        {loadError ? (
          <p
            role="alert"
            className="rounded-lg border border-gray-200 bg-status-negative-bg px-4 py-3 text-sm text-status-negative"
          >
            {loadError.message}
          </p>
        ) : null}

        <Field
          id="llmProvider"
          label="AI provider"
          hint="Pick the provider the key below belongs to — a key from a different provider is rejected on save."
          error={errors.provider?.message}
        >
          {({ id, invalid }) => (
            <Controller
              control={control}
              name="provider"
              render={({ field }) => (
                <Select
                  id={id}
                  options={PROVIDER_OPTIONS}
                  // Radix has no empty-string value, so "nothing selected"
                  // is `undefined` here and the placeholder shows instead.
                  value={field.value === UNSELECTED_PROVIDER ? undefined : field.value}
                  placeholder="Select a provider"
                  // Same guard as the preferences form's Select: the hidden
                  // native <select> Radix renders can emit a spurious `''`
                  // while the dropdown is closed, which would otherwise be
                  // taken as a real (invalid) provider choice.
                  onValueChange={(next) => {
                    if (PROVIDER_VALUES.has(next)) {
                      field.onChange(next)
                    }
                  }}
                  invalid={invalid}
                  aria-label="AI provider"
                />
              )}
            />
          )}
        </Field>

        <Field
          id="llmModel"
          label="Model"
          optional
          hint="Leave blank to use the recommended default model for the provider you picked. Set it only if you want a specific model on your account."
          error={errors.model?.message}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              placeholder="e.g. claude-sonnet-4-5"
              {...register('model')}
            />
          )}
        </Field>

        <Field
          id="llmApiKey"
          label="API key"
          // Read-only context, never a value inside the input: the field
          // itself stays empty so a save can only ever send a freshly typed
          // key.
          badge={keyHint ? `Current key ${keyHint}` : null}
          hint="Stored encrypted and never shown again. Every save re-checks the key with the provider, so enter it again even if you only changed the model."
          error={errors.apiKey?.message}
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="password"
              autoComplete="off"
              aria-describedby={describedBy}
              invalid={invalid}
              placeholder="Paste your API key"
              {...register('apiKey')}
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

      {/* Deliberately not `sticky` like the preferences save bar — two sticky
          bars on one page would stack and fight for the same edge. */}
      <div className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-gray-500">
          Saving makes one test call to the provider, so an expired or mistyped key is caught here rather
          than mid-pipeline.
        </p>
        <div className="flex shrink-0 items-center gap-3">
          <Button type="submit" variant="secondary" isLoading={updateLlmConfig.isPending}>
            Save AI provider
          </Button>
        </div>
      </div>
    </form>
  )
}
