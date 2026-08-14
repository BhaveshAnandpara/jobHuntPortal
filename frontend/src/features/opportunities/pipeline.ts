/**
 * Status-grouping and polling-stop-condition helpers for the Opportunities
 * surfaces. Pure display/partitioning logic over the real `ApplicationStatus`
 * enum — never a new backend concept, matching
 * docs/frontend/user-flows.md#application-tracker-ux exactly.
 *
 * Owner: frontend-opportunities-agent.
 */

import type { ApplicationResponse, ApplicationStatus } from '../../api/types'

export type StatusTab = 'active' | 'applied' | 'closed' | 'all'

export const STATUS_TABS: { id: StatusTab; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'applied', label: 'Applied' },
  { id: 'closed', label: 'Closed' },
  { id: 'all', label: 'All' },
]

/**
 * The four-tab grouping from user-flows.md#application-tracker-ux. Built on
 * the single unfiltered `GET /applications` fetch, partitioned client-side —
 * there is no server-side "status in [...]" query variant.
 */
const TAB_STATUS_SETS: Record<Exclude<StatusTab, 'all'>, ApplicationStatus[]> = {
  active: [
    'DISCOVERED',
    'MATCHED',
    'SHORTLISTED',
    'CONTACT_SEARCH',
    'CONTACT_FOUND',
    'OUTREACH_GENERATED',
    'OUTREACH_APPROVED',
    'OUTREACH_SENT',
    'REFERRED',
  ],
  applied: ['APPLIED', 'INTERVIEW'],
  closed: ['OFFER', 'REJECTED', 'IGNORED', 'WITHDRAWN'],
}

export function matchesStatusTab(status: ApplicationStatus, tab: StatusTab): boolean {
  return tab === 'all' ? true : TAB_STATUS_SETS[tab].includes(status)
}

/**
 * Opportunity Detail's 3s poll stop condition per
 * docs/frontend/async-workflows.md's table: stop once *this* application's
 * status reaches a terminal value, or `OUTREACH_SENT` (the automated
 * chain's last unattended step — everything past it is manual, so there's
 * nothing left to poll for until the user acts).
 */
const STOP_POLLING_STATUSES: ApplicationStatus[] = [
  'OFFER',
  'REJECTED',
  'IGNORED',
  'WITHDRAWN',
  'OUTREACH_SENT',
]

export function isApplicationSettled(application: ApplicationResponse | undefined): boolean {
  return application !== undefined && STOP_POLLING_STATUSES.includes(application.status)
}
