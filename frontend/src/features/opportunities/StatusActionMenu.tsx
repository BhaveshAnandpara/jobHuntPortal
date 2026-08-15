/**
 * Status Action Menu — Opportunity Detail. Manual `ApplicationStatus`
 * transitions via `useUpdateApplicationStatus()`, pre-filtered to what
 * `statusTransitions.ts` documents as valid manual transitions from the
 * current status (a UX nicety, not the real authority — see
 * docs/frontend/architecture.md#thin-client-principle).
 *
 * On a 400/409 rejection from the backend (e.g. a stale menu after a
 * concurrent update elsewhere), the mutation's own error message is shown
 * inline and `onSettled` is called to refetch the current application state
 * — this never locally forces the status change; the visible status only
 * ever comes from the refetched `ApplicationResponse` in the parent page.
 * See docs/frontend/error-handling.md#invalid-state-transition-ux.
 *
 * Owner: frontend-opportunities-agent.
 */

import { useEffect, useState } from 'react'
import { Button, Card, FieldError, Select } from '../../components'
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
    updateStatus.mutate(
      {
        applicationId,
        body: { new_status: selected as ApplicationStatus, applied_date: null, notes: null },
      },
      {
        onError: () => {
          onSettled()
        },
      },
    )
  }

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Update status</h2>
      {options.length === 0 ? (
        <p className="text-sm text-gray-500">No further manual status changes are available.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <Select
            aria-label="New status"
            value={selected}
            onValueChange={setSelected}
            options={options.map((status) => ({ value: status, label: getStatusPresentation(status).label }))}
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
          {updateStatus.isError ? <FieldError message={toApiError(updateStatus.error).message} /> : null}
        </div>
      )}
    </Card>
  )
}
