/**
 * The one place outreach *status copy* is worded, shared by the queue list
 * (`OutreachQueuePage`) and the review panel (`OutreachReviewPanel`).
 *
 * This file exists because of the invariant in
 * docs/frontend/frontend-revamp-spec.md#26-invariant-that-must-survive-the-redesign:
 * `APPROVED` and `SENT` are two different states and must never be conflated
 * in copy. Approving only asks the backend to send (it publishes
 * `outreach.approved`); the actual send happens later, in a separate
 * Kafka-driven step, and only then does the record become `SENT`. Keeping the
 * wording in one module means the list and the panel can't drift into
 * describing an approved draft as if it had already gone out.
 *
 * Nothing here derives state — every string is a pure function of the
 * server-fetched `status`/timestamps on an `OutreachResponse`. There is no
 * "optimistically sent" branch to write, because the frontend never knows a
 * send happened until a fetch says so.
 *
 * Owner: frontend-outreach-agent.
 */

import { formatDateTime } from '../../utils/format'
import type { OutreachResponse } from '../../api/types'

/**
 * The full sentence shown in the review panel under the status badge.
 * `null` for `PENDING_APPROVAL`/`DRAFT`, where the badge ("Needs your
 * review") already says everything and a second sentence would just be
 * filler.
 */
export function getStatusHelperCopy(status: OutreachResponse['status']): string | null {
  switch (status) {
    case 'APPROVED':
      // Deliberately NOT "Sent" — see this file's header.
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

/**
 * The short delivery line shown under the status badge in the queue list —
 * the list-level counterpart of `getStatusHelperCopy`, kept terse enough for
 * a table cell. It is what makes an approved row and a sent row readable as
 * different rows at a glance, rather than two green badges whose labels
 * differ by one word.
 *
 * `sent_at` / `decided_at` come straight off the record; when the backend
 * hasn't set a timestamp yet the label degrades to the bare state rather than
 * inventing a time.
 */
export function getDeliveryNote(outreach: OutreachResponse): string | null {
  switch (outreach.status) {
    case 'APPROVED':
      return 'Awaiting send'
    case 'SENT':
      return outreach.sent_at ? `Delivered ${formatDateTime(outreach.sent_at)}` : 'Delivered'
    case 'SEND_FAILED':
      return 'Needs manual follow-up'
    case 'REJECTED':
      return 'Never sent'
    default:
      return null
  }
}
