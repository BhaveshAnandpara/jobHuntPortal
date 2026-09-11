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
 * T9 (docs/frontend/frontend-revamp-spec.md) restyle: the panel is now a
 * three-band card — identity header, body (meta + state + message), and an
 * action bar that only exists while the record is actionable. Exactly one
 * filled/primary control (Approve) lives in that bar; Edit and Reject sit
 * beside it as visible secondary controls, never hidden behind an overflow
 * menu, because a reviewer must be able to see all three options the gate
 * offers without hunting. Status copy moved to `outreachCopy.ts` so the
 * queue list and this panel word Approved-vs-Sent identically.
 *
 * Owner: frontend-outreach-agent.
 * Input: `outreach` — the full record, fetched by the calling page. This
 *        component never fetches its own `OutreachResponse`; both callers
 *        (queue selection, deep-link detail page) stay the single source
 *        of truth for *which* record is shown, and pass fresh props down
 *        after every refetch (including the 409-triggered refetch built
 *        into the mutation hooks in `api/outreach.ts`).
 *        Optional `applicationId` (passed straight through to
 *        `useApproveOutreach` for the more targeted cache invalidation
 *        described there, when the caller happens to have it).
 *        Optional `onConflict` — see its prop doc.
 */

import { useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { AlertTriangle, Ban, CheckCircle2, Clock, Pencil, PencilLine } from 'lucide-react'
import { Button, Card, Dialog, FieldError, Skeleton, StatusBadge, Textarea } from '../../components'
import { useApproveOutreach, useEditOutreach, useRejectOutreach } from '../../api/outreach'
import { useJob } from '../../api/jobs'
import { useContacts } from '../../api/contacts'
import { toApiError } from '../../api/client'
import { requiredString } from '../../utils/validation'
import { formatDateTime } from '../../utils/format'
import { cn } from '@/lib/utils'
import { getChannelLabel } from './channelLabel'
import { getStatusHelperCopy } from './outreachCopy'
import type { OutreachResponse } from '../../api/types'

export type OutreachReviewPanelProps = {
  outreach: OutreachResponse
  applicationId?: string
  /**
   * Called once, after a `409 CONFLICT` from approve/edit/reject, for a
   * caller whose view of this record comes from a *list* query
   * (`OutreachQueuePage`) rather than `useOutreachItem`. The 409 refetch
   * built into `api/outreach.ts` invalidates `queryKeys.outreachItem(...)`,
   * which that caller isn't subscribed to, so without this hook the queue
   * would keep showing the stale record until its next 5s poll. This is a
   * refetch request, never a retry of the failed action — see
   * error-handling.md's 409 row.
   */
  onConflict?: () => void
}

const editSchema = z.object({ message: requiredString })
type EditFormValues = z.infer<typeof editSchema>

const ACTIONABLE_STATUSES = new Set<OutreachResponse['status']>(['PENDING_APPROVAL', 'EDITED'])

/**
 * How each decided state *looks*, on top of the copy in `outreachCopy.ts`.
 *
 * `APPROVED` and `SENT` deliberately get different icons and different color
 * tokens even though `utils/status.ts` maps both to the `positive` badge
 * category (which is correct for the badge — both are good outcomes). At a
 * glance, "approved, waiting on the sender" reads as an in-flight clock and
 * "sent" reads as a completed check, so the two can never be mistaken for
 * each other in the panel even if someone skims past the wording.
 */
const STATUS_NOTE_APPEARANCE: Partial<
  Record<OutreachResponse['status'], { Icon: typeof Clock; className: string }>
> = {
  APPROVED: { Icon: Clock, className: 'bg-status-progress-bg text-status-progress' },
  SENT: { Icon: CheckCircle2, className: 'bg-status-positive-bg text-status-positive' },
  SEND_FAILED: { Icon: AlertTriangle, className: 'bg-status-negative-bg text-status-negative' },
  REJECTED: { Icon: Ban, className: 'bg-status-neutral-bg text-status-neutral' },
  EDITED: { Icon: PencilLine, className: 'bg-status-attention-bg text-status-attention' },
}

export function OutreachReviewPanel({ outreach, applicationId, onConflict }: OutreachReviewPanelProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [conflictMessage, setConflictMessage] = useState<string | null>(null)
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false)
  const rejectTriggerRef = useRef<HTMLButtonElement>(null)

  const jobQuery = useJob(outreach.job_id)
  const contactsQuery = useContacts(outreach.job_id)

  const approveMutation = useApproveOutreach()
  const rejectMutation = useRejectOutreach()
  const editMutation = useEditOutreach()

  const displayedMessage = outreach.final_message ?? outreach.draft_message
  const isActionable = ACTIONABLE_STATUSES.has(outreach.status)
  const helperCopy = getStatusHelperCopy(outreach.status)
  const noteAppearance = STATUS_NOTE_APPEARANCE[outreach.status]

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
      // `outreachItem` on 409; this component surfaces the message and
      // lets a list-driven caller refresh its own copy of the record via
      // `onConflict`. Neither path re-sends the failed action.
      setConflictMessage(apiError.message)
      onConflict?.()
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
      { outreachId: outreach.id, applicationId, body: { final_message: null } },
      { onError: handleMutationError },
    )
  }

  function handleReject() {
    setRejectDialogOpen(false)
    setConflictMessage(null)
    rejectMutation.mutate({ outreachId: outreach.id }, { onError: handleMutationError })
  }

  function onEditSubmit(values: EditFormValues) {
    setConflictMessage(null)
    editMutation.mutate(
      { outreachId: outreach.id, body: { message: values.message } },
      {
        onSuccess: () => setIsEditing(false),
        onError: handleMutationError,
      },
    )
  }

  const contact = contactsQuery.data?.find((candidate) => candidate.id === outreach.contact_id)

  return (
    <Card className="flex flex-col overflow-hidden p-0">
      <header className="flex items-start justify-between gap-4 border-b border-gray-200 bg-gray-50/60 px-5 py-4">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-gray-500 uppercase">Recipient</p>
          {contactsQuery.isLoading ? (
            <Skeleton className="mt-1.5 h-5 w-40" />
          ) : contact ? (
            <p className="mt-1 truncate text-sm font-semibold text-gray-900">
              {contact.full_name}
              {contact.headline ? (
                <span className="font-normal text-gray-500"> · {contact.headline}</span>
              ) : null}
            </p>
          ) : (
            <p className="mt-1 text-sm text-gray-500">Contact {outreach.contact_id}</p>
          )}
        </div>
        <StatusBadge status={outreach.status} />
      </header>

      <div className="flex flex-col gap-4 px-5 py-4">
        {/*
          Plain spans rather than a <dl> on purpose: each "Label: value" pair
          has to stay a single text run so it reads as one phrase to screen
          readers (and to text-based assertions) instead of being split into
          two unrelated nodes.
        */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span>Channel: {getChannelLabel(outreach.channel)}</span>
          <span aria-hidden className="text-gray-300">
            |
          </span>
          <span className="min-w-0 truncate">
            Target:{' '}
            {jobQuery.isLoading
              ? '…'
              : jobQuery.data
                ? `${jobQuery.data.title} · ${jobQuery.data.company}`
                : `Job ${outreach.job_id}`}
          </span>
          <span aria-hidden className="text-gray-300">
            |
          </span>
          <span>Generated {formatDateTime(outreach.generated_at)}</span>
        </div>

        {helperCopy ? (
          <div
            className={cn(
              'flex items-center gap-2 rounded-md px-3 py-2 text-sm',
              noteAppearance?.className ?? 'bg-status-neutral-bg text-status-neutral',
            )}
          >
            {noteAppearance ? <noteAppearance.Icon className="h-4 w-4 shrink-0" aria-hidden /> : null}
            <p className="font-medium">{helperCopy}</p>
          </div>
        ) : null}

        {conflictMessage ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-status-attention/30 bg-status-attention-bg px-3 py-2 text-sm text-status-attention"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="font-medium">{conflictMessage}</p>
              <p className="mt-0.5 text-status-attention/90">
                Your click was not applied. The panel now shows this draft&rsquo;s current state.
              </p>
            </div>
          </div>
        ) : null}

        {isEditing ? (
          <form onSubmit={handleSubmit(onEditSubmit)} noValidate className="flex flex-col gap-2">
            <label htmlFor="outreach-edit-message" className="text-xs font-medium text-gray-700">
              Draft message
            </label>
            <Textarea
              id="outreach-edit-message"
              rows={8}
              invalid={Boolean(errors.message)}
              {...register('message')}
            />
            <FieldError message={errors.message?.message} />
            <p className="text-xs text-gray-500">
              Saving records your wording on the draft and marks it edited. It is still not approved.
            </p>
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
          <div className="flex flex-col gap-1.5">
            <p className="text-xs font-medium tracking-wide text-gray-500 uppercase">
              {outreach.final_message ? 'Edited message' : 'Generated draft'}
            </p>
            <p className="rounded-md border border-gray-200 bg-gray-50 p-4 text-sm leading-relaxed whitespace-pre-wrap text-gray-900">
              {displayedMessage}
            </p>
          </div>
        )}
      </div>

      {isActionable && !isEditing ? (
        <footer className="flex flex-col gap-2 border-t border-gray-200 px-5 py-4">
          {/*
            One filled action. Approve is the only control in this product
            that can lead to a message leaving the account, so it is the only
            one styled as primary; Edit and Reject stay visible beside it
            rather than collapsing into a menu.
          */}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" isLoading={approveMutation.isPending} onClick={handleApprove}>
              Approve
            </Button>
            <Button type="button" variant="secondary" onClick={startEditing}>
              <Pencil className="h-4 w-4" aria-hidden />
              Edit
            </Button>
            <Button ref={rejectTriggerRef} variant="destructive" onClick={() => setRejectDialogOpen(true)}>
              Reject
            </Button>
          </div>
          <p className="text-xs text-gray-500">
            Approving is the only way this message ever goes out. Delivery happens afterwards, on its
            own — the status here updates once it does.
          </p>
        </footer>
      ) : null}

      <Dialog
        open={rejectDialogOpen}
        onOpenChange={setRejectDialogOpen}
        title="Reject this outreach?"
        description="This cannot be undone. The draft will be marked rejected and removed from the review queue."
        triggerRef={rejectTriggerRef}
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
