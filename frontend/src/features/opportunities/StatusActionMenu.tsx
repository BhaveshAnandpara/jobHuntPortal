/**
 * Status Action Menu — Opportunity Detail. Manual `ApplicationStatus`
 * transitions via `useUpdateApplicationStatus()`, pre-filtered to what
 * `statusTransitions.ts` documents as valid manual transitions from the
 * current status (a UX nicety, not the real authority — see
 * docs/frontend/architecture.md#thin-client-principle).
 *
 * The rejection this endpoint actually produces is `400 VALIDATION_ERROR`,
 * not `409` — `PATCH /applications/{id}/status` validates the transition
 * against the state machine and rejects an invalid one as a validation
 * failure (docs/frontend/error-handling.md#invalid-state-transition-ux-a-specific-case-of-400-validation_error).
 * `409` belongs to the outreach approve/reject/edit endpoints, which are not
 * reachable from this menu, so no 409-specific branch is invented here. The
 * handling is written against the response rather than the code anyway: any
 * rejection shows the server's own `message` inline and calls `onSettled`, so
 * the parent refetches `GET /applications/{id}` and the menu re-derives its
 * options from whatever the server now says the status is. The visible status
 * is never forced locally, on success or on failure — a stale menu resolves
 * itself by learning the real status, not by retrying.
 *
 * T8 (docs/frontend/frontend-revamp-spec.md) restyle. Same one mutation, same
 * pre-filter, plus the states the panel was missing:
 * - a success confirmation naming the status that was actually applied
 *   (previously the only feedback was the badge elsewhere on the page
 *   changing);
 * - a terminal state that names the status it is terminal *in*, instead of a
 *   generic "nothing available" line;
 * - the rejection message is announced (`role="alert"` via `FieldError`) and
 *   sits with the control that produced it.
 *
 * Owner: frontend-opportunities-agent.
 */

import { useEffect, useState } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { Button, FieldError, Select } from '../../components'
import { DetailPanel } from './DetailPanel'
import { useUpdateApplicationStatus } from '../../api/tracking'
import { toApiError } from '../../api/client'
import { manualTransitionsFrom } from './statusTransitions'
import type { ApplicationStatus } from '../../api/types'
import { getStatusPresentation } from '../../utils/status'

type StatusActionMenuProps = {
  applicationId: string
  currentStatus: ApplicationStatus
  /** Called after a failed transition so the caller can refetch current state. */
  onSettled: () => void
}

export function StatusActionMenu({ applicationId, currentStatus, onSettled }: StatusActionMenuProps) {
  const options = manualTransitionsFrom(currentStatus)
  const [selected, setSelected] = useState<string | undefined>(options[0])
  // The status the server confirmed, so the confirmation line can name it
  // without reading it back off the (independently refetched) application.
  const [confirmedStatus, setConfirmedStatus] = useState<ApplicationStatus | null>(null)
  const updateStatus = useUpdateApplicationStatus()

  // Reset the selection whenever the underlying status changes (a
  // successful update, or a refetch after a rejected one) so the menu never
  // shows a now-invalid option pre-selected.
  useEffect(() => {
    setSelected(manualTransitionsFrom(currentStatus)[0])
  }, [currentStatus])

  const handleSubmit = () => {
    if (!selected) {
      return
    }
    const requested = selected as ApplicationStatus
    setConfirmedStatus(null)
    updateStatus.mutate(
      {
        applicationId,
        body: { new_status: requested, applied_date: null, notes: null },
      },
      {
        onSuccess: (application) => {
          // Named from the server's response, not from `requested` — if the
          // backend settled on something else, that is what gets reported.
          setConfirmedStatus(application.status)
        },
        onError: () => {
          onSettled()
        },
      },
    )
  }

  return (
    <DetailPanel
      title="Update status"
      description="For the steps the pipeline can't observe — applying, interviewing, or closing this out."
    >
      {options.length === 0 ? (
        // Terminal: a real disabled state, and it says which status it is
        // terminal in rather than leaving the user to infer it.
        <p className="text-sm text-gray-500">
          This opportunity is marked {getStatusPresentation(currentStatus).label}. There are no further
          status changes to make.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <Select
            aria-label="New status"
            value={selected}
            onValueChange={(value) => {
              setConfirmedStatus(null)
              setSelected(value)
            }}
            options={options.map((status) => ({
              value: status,
              label: getStatusPresentation(status).label,
            }))}
            placeholder="Choose a status"
          />
          <Button
            type="button"
            variant="primary"
            isLoading={updateStatus.isPending}
            onClick={handleSubmit}
            disabled={!selected}
            className="self-start"
          >
            Update
          </Button>

          {updateStatus.isError ? (
            <div>
              <FieldError message={toApiError(updateStatus.error).message} />
              <p className="mt-1 text-xs text-gray-500">
                The options above now reflect this opportunity&apos;s current status.
              </p>
            </div>
          ) : null}

          {confirmedStatus && !updateStatus.isError ? (
            <p role="status" className="flex items-center gap-1.5 text-xs text-status-positive">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
              Status updated to {getStatusPresentation(confirmedStatus).label}.
            </p>
          ) : null}
        </div>
      )}
    </DetailPanel>
  )
}
