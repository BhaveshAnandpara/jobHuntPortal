/**
 * The one Outreach review UI — rendered inline by `OutreachQueuePage` (list
 * + reading pane) and by `OutreachReviewPage` (deep link), never
 * duplicated. See docs/frontend/user-flows.md#outreach-review-ux,
 * docs/frontend/error-handling.md's `409 CONFLICT` row, and
 * docs/architecture/state-machines.md's Outreach lifecycle.
 *
 * This is the human-approval gate: no action beyond approve/edit/reject
 * exists anywhere below. There is no "send" button and this component
 * never calls any transport directly — sending is an async,
 * Kafka-triggered backend side effect of approval (see
 * docs/frontend/architecture.md#thin-client-principle). Nothing here
 * mutates on mount or on a timer; every mutation fires from a direct user
 * click.
 *
 * Approve-vs-edit UX choice (documented per the task brief's request to be
 * explicit): this implementation keeps Edit and Approve as two SEPARATE
 * calls, matching user-flows.md's diagram literally — `Edit` always goes
 * through `POST .../edit` first (status -> `EDITED`, still not decided),
 * and `Approve` is always a distinct, later click that sends
 * `{final_message: null}` (the backend approves whatever `final_message`/
 * `draft_message` is already on the record server-side). There is no
 * "combined" edit+approve-in-one-call path.
 *
 * Owner: frontend-outreach-agent.
 * Input: `outreach` — the full record, fetched by the calling page. This
 *        component never fetches its own `OutreachResponse`; both callers
 *        (queue selection, deep-link detail page) stay the single source
 *        of truth for *which* record is shown, and pass fresh props down
 *        after every refetch (including the 409-triggered refetch built
 *        into the mutation hooks in `api/outreach.ts`).
 *        `userId`, optional `applicationId` (passed straight through to
 *        `useApproveOutreach` for the more targeted cache invalidation
 *        described there, when the caller happens to have it).
 */

import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { Pencil } from 'lucide-react'
import { Button, Card, Dialog, FieldError, Skeleton, StatusBadge, Textarea } from '../../components'
import { useApproveOutreach, useEditOutreach, useRejectOutreach } from '../../api/outreach'
import { useJob } from '../../api/jobs'
import { useContacts } from '../../api/contacts'
import { toApiError } from '../../api/client'
import { requiredString } from '../../utils/validation'
import { formatDateTime } from '../../utils/format'
import { getChannelLabel } from './channelLabel'
import type { OutreachResponse } from '../../api/types'

export type OutreachReviewPanelProps = {
  outreach: OutreachResponse
  userId: string
  applicationId?: string
}

const editSchema = z.object({ message: requiredString })
type EditFormValues = z.infer<typeof editSchema>

const ACTIONABLE_STATUSES = new Set<OutreachResponse['status']>(['PENDING_APPROVAL', 'EDITED'])

/**
 * Every string here is derived purely from the server-fetched `status` —
 * never from local "I just clicked approve" state — so "Sent" can only
 * ever appear once `status` has actually been refetched as `SENT`. This is
 * the concrete mechanism behind "Generated != Sent": approving only ever
 * moves `status` to `APPROVED` (never `SENT`) as far as this component's
 * own API responses are concerned — `SENT` only appears via a later,
 * independent fetch of the real record (see async-workflows.md's note that
 * approval-to-send latency is a real, uncollapsed gap, not something to
 * paper over client-side).
 */
function statusHelperCopy(status: OutreachResponse['status']): string | null {
  switch (status) {
    case 'APPROVED':
      return 'Approved — will be sent shortly.'
    case 'SENT':
      return 'Sent.'
    case 'SEND_FAILED':
      return 'Sending failed. This requires manual follow-up — no automatic retry.'
    case 'REJECTED':
      return 'Rejected.'
    case 'EDITED':
      return 'Edited — still needs your approval or rejection.'
    default:
      return null
  }
}

export function OutreachReviewPanel({ outreach, userId, applicationId }: OutreachReviewPanelProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [conflictMessage, setConflictMessage] = useState<string | null>(null)
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false)

  const jobQuery = useJob(outreach.job_id)
  const contactsQuery = useContacts(outreach.job_id)

  const approveMutation = useApproveOutreach()
  const rejectMutation = useRejectOutreach()
  const editMutation = useEditOutreach()

  const displayedMessage = outreach.final_message ?? outreach.draft_message
  const isActionable = ACTIONABLE_STATUSES.has(outreach.status)

  // A conflict banner explains *why the click that was just attempted
  // against this same record* failed — it must survive the 409-triggered
  // refetch built into every hook above (that refetch is what makes the
  // banner accurate in the first place: it changes `outreach.status`/
  // `decided_at` to the real current state while the banner is still
  // showing why the stale click didn't apply). Clearing it here on
  // `status`/`decided_at` would race that exact refetch and erase the
  // banner within the same tick it appeared (Step 12 finding). Instead,
  // only clear when the user has actually moved on: to a different
  // outreach record entirely (see below), or by starting another action
  // against this one (see `setConflictMessage(null)` at the top of
  // `handleApprove`/`handleReject`/`onEditSubmit`).
  useEffect(() => {
    setConflictMessage(null)
  }, [outreach.id])

  // If the record stops being actionable out from under an in-progress
  // edit (e.g. someone else decided it while this tab had the form open),
  // don't leave a dead edit form on screen.
  useEffect(() => {
    if (!isActionable) {
      setIsEditing(false)
    }
  }, [isActionable])

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
    defaultValues: { message: displayedMessage },
  })

  function handleMutationError(error: unknown) {
    const apiError = toApiError(error)
    if (apiError.status === 409) {
      // Specific inline message, not a generic toast — see
      // error-handling.md's 409 row. The hooks already refetch
      // `outreachItem` on 409; this component just needs to surface the
      // message and let the next render (fresh `outreach` prop) show the
      // real current state.
      setConflictMessage(apiError.message)
    } else {
      toast.error(apiError.message)
    }
  }

  function startEditing() {
    reset({ message: displayedMessage })
    setIsEditing(true)
  }

  function handleApprove() {
    setConflictMessage(null)
    approveMutation.mutate(
      { outreachId: outreach.id, userId, applicationId, body: { final_message: null } },
      { onError: handleMutationError },
    )
  }

  function handleReject() {
    setRejectDialogOpen(false)
    setConflictMessage(null)
    rejectMutation.mutate({ outreachId: outreach.id, userId }, { onError: handleMutationError })
  }

  function onEditSubmit(values: EditFormValues) {
    setConflictMessage(null)
    editMutation.mutate(
      { outreachId: outreach.id, userId, body: { message: values.message } },
      {
        onSuccess: () => setIsEditing(false),
        onError: handleMutationError,
      },
    )
  }

  const contact = contactsQuery.data?.find((candidate) => candidate.id === outreach.contact_id)

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium text-gray-500">Recipient</p>
          {contactsQuery.isLoading ? (
            <Skeleton className="mt-1 h-5 w-40" />
          ) : contact ? (
            <p className="text-sm font-medium text-gray-900">
              {contact.full_name}
              {contact.headline ? (
                <span className="font-normal text-gray-500"> · {contact.headline}</span>
              ) : null}
            </p>
          ) : (
            <p className="text-sm text-gray-500">Contact {outreach.contact_id}</p>
          )}
        </div>
        <StatusBadge status={outreach.status} />
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500">
        <span>Channel: {getChannelLabel(outreach.channel)}</span>
        <span>
          Target:{' '}
          {jobQuery.isLoading
            ? '…'
            : jobQuery.data
              ? `${jobQuery.data.title} · ${jobQuery.data.company}`
              : `Job ${outreach.job_id}`}
        </span>
        <span>Generated {formatDateTime(outreach.generated_at)}</span>
      </div>

      {statusHelperCopy(outreach.status) ? (
        <p className="text-sm text-gray-600">{statusHelperCopy(outreach.status)}</p>
      ) : null}

      {conflictMessage ? (
        <p role="alert" className="rounded-md bg-status-attention-bg px-3 py-2 text-sm text-status-attention">
          {conflictMessage}
        </p>
      ) : null}

      {isEditing ? (
        <form onSubmit={handleSubmit(onEditSubmit)} noValidate className="flex flex-col gap-2">
          <Textarea rows={6} invalid={Boolean(errors.message)} {...register('message')} />
          <FieldError message={errors.message?.message} />
          <div className="flex gap-2">
            <Button type="submit" variant="primary" isLoading={editMutation.isPending}>
              Save edit
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                reset({ message: displayedMessage })
                setIsEditing(false)
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap rounded-md bg-gray-50 p-3 text-sm text-gray-900">{displayedMessage}</p>
      )}

      {isActionable && !isEditing ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" isLoading={approveMutation.isPending} onClick={handleApprove}>
            Approve
          </Button>
          <Button type="button" variant="secondary" onClick={startEditing}>
            <Pencil className="h-4 w-4" aria-hidden />
            Edit
          </Button>
          <Button variant="destructive" onClick={() => setRejectDialogOpen(true)}>
            Reject
          </Button>
        </div>
      ) : null}

      <Dialog
        open={rejectDialogOpen}
        onOpenChange={setRejectDialogOpen}
        title="Reject this outreach?"
        description="This cannot be undone. The draft will be marked rejected and removed from the review queue."
      >
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={() => setRejectDialogOpen(false)}>
            Cancel
          </Button>
          <Button variant="destructive" isLoading={rejectMutation.isPending} onClick={handleReject}>
            Reject
          </Button>
        </div>
      </Dialog>
    </Card>
  )
}
