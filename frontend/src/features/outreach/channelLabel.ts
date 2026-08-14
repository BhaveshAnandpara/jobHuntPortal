/**
 * Pure label mapping for `OutreachChannel` — not a lifecycle status (so it
 * deliberately does not go through `StatusBadge`/`utils/status.ts`, which
 * are reserved for the backend's status enums per
 * docs/frontend/agent-ownership.md's "must not implement its own
 * `StatusBadge`" rule — a channel is not a status). Kept here, not inline,
 * so both `OutreachReviewPanel` and `OutreachQueuePage` render the exact
 * same channel copy.
 *
 * Owner: frontend-outreach-agent.
 */

import type { OutreachChannel } from '../../api/types'

const CHANNEL_LABELS: Record<OutreachChannel, string> = {
  EMAIL: 'Email',
  LINKEDIN_MESSAGE: 'LinkedIn Message',
  LINKEDIN_CONNECTION_REQUEST: 'LinkedIn Connection Request',
  OTHER: 'Other',
}

export function getChannelLabel(channel: OutreachChannel): string {
  return CHANNEL_LABELS[channel]
}
