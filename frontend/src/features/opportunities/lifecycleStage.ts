/**
 * Progressive panel unlocking for Opportunity Detail — T8
 * (docs/frontend/frontend-revamp-spec.md).
 *
 * The detail page shows one panel per pipeline stage, but a panel can only
 * honestly show data for a stage that has actually run. Before that, the
 * panel must say so ("contact search hasn't started"), which is a *different*
 * thing from "loading" (a request is in flight) and from "empty" (the stage
 * ran and genuinely found nothing) — spec Section 4, T8's design
 * requirements.
 *
 * Deciding which of those three a panel is in needs one honest question
 * answered: how far along the automated chain has this opportunity actually
 * got? That is what this module answers, from real server fields only:
 *
 * 1. `ApplicationStatus` — but only for the statuses that *are* points on the
 *    automated chain. `APPLIED`/`INTERVIEW`/`OFFER`/`REJECTED`/`IGNORED`/
 *    `WITHDRAWN` are reachable manually from almost anywhere (see
 *    statusTransitions.ts), so they say nothing about whether contact search
 *    ever ran. They rank as `null` here rather than being force-fitted onto
 *    the chain.
 * 2. `GET /applications/{id}/history` — the real audit trail. If the row ever
 *    passed through `CONTACT_FOUND`, contact search ran, whatever the status
 *    reads now. Both `to_status` and `from_status` count: a history row
 *    `CONTACT_FOUND -> WITHDRAWN` proves `CONTACT_FOUND` happened even though
 *    no row has it as a destination.
 * 3. `match_score != null` on the application itself — matching produced a
 *    number, so matching ran. This is the one piece of evidence available
 *    before the (separate, independently failable) history query resolves.
 *
 * When none of the three can answer — an off-chain status whose history
 * hasn't loaded or failed to load — the answer is `unknown`, and callers
 * render the ordinary data panel rather than asserting "hasn't started yet".
 * Claiming a stage never ran on the strength of a failed side query would be
 * inventing a state; showing the real (possibly empty) panel is not.
 *
 * Owner: frontend-opportunities-agent.
 */

import type {
  ApplicationHistoryResponse,
  ApplicationResponse,
  ApplicationStatus,
} from '../../api/types'

/**
 * The automated chain in order, straight from
 * docs/architecture/state-machines.md#opportunity-lifecycle. Index is the
 * rank. Statuses not listed here are manual/terminal and deliberately have no
 * rank — see this file's header.
 */
const CHAIN: ApplicationStatus[] = [
  'DISCOVERED',
  'MATCHED',
  'SHORTLISTED',
  'CONTACT_SEARCH',
  'CONTACT_FOUND',
  'OUTREACH_GENERATED',
  'OUTREACH_APPROVED',
  'OUTREACH_SENT',
  'REFERRED',
]

/** `null` for a status that is not a point on the automated chain. */
export function chainRank(status: ApplicationStatus | null | undefined): number | null {
  if (!status) {
    return null
  }
  const index = CHAIN.indexOf(status)
  return index === -1 ? null : index
}

/** The panels whose content only exists once a given stage has run. */
export type PipelineStage = 'match' | 'contacts' | 'outreach'

/**
 * The first chain status at which each stage's data can exist. Contacts is
 * `CONTACT_SEARCH`, not `CONTACT_FOUND` — once the search is running, an
 * empty contact list is a real (if provisional) answer, so the panel switches
 * from "hasn't started" to its own loading/empty handling there.
 */
const STAGE_ENTRY: Record<PipelineStage, ApplicationStatus> = {
  match: 'MATCHED',
  contacts: 'CONTACT_SEARCH',
  outreach: 'OUTREACH_GENERATED',
}

export type StageAvailability =
  /** The stage has run (or is running) — render the real panel. */
  | 'reached'
  /** The stage provably hasn't started — render the "not yet" placeholder. */
  | 'not-reached'
  /** Not determinable from what the server has told us — render the real panel. */
  | 'unknown'

/**
 * How far along the chain this opportunity provably got, or `null` when
 * nothing available says. See this file's header for the three evidence
 * sources, in the order they are combined here (the max wins — evidence of
 * having reached a later stage is never cancelled by a status that ranks
 * earlier or not at all).
 */
export function furthestChainRank(
  application: ApplicationResponse | undefined,
  history: ApplicationHistoryResponse[] | undefined,
): number | null {
  const ranks: number[] = []

  const currentRank = chainRank(application?.status)
  if (currentRank !== null) {
    ranks.push(currentRank)
  }

  // A score only exists because the matching workflow produced one.
  if (application?.match_score != null) {
    const matchedRank = chainRank('MATCHED')
    if (matchedRank !== null) {
      ranks.push(matchedRank)
    }
  }

  for (const entry of history ?? []) {
    const toRank = chainRank(entry.to_status)
    if (toRank !== null) {
      ranks.push(toRank)
    }
    const fromRank = chainRank(entry.from_status)
    if (fromRank !== null) {
      ranks.push(fromRank)
    }
  }

  return ranks.length === 0 ? null : Math.max(...ranks)
}

export function getStageAvailability(
  stage: PipelineStage,
  application: ApplicationResponse | undefined,
  history: ApplicationHistoryResponse[] | undefined,
): StageAvailability {
  const reached = furthestChainRank(application, history)
  if (reached === null) {
    return 'unknown'
  }
  const required = chainRank(STAGE_ENTRY[stage])
  // `required` is a chain status by construction, so this is never null; the
  // guard keeps the types honest rather than asserting.
  if (required === null) {
    return 'unknown'
  }
  return reached >= required ? 'reached' : 'not-reached'
}

/**
 * The one place callers turn availability into "do I render data or a
 * placeholder". `unknown` renders data — see this file's header for why an
 * unanswerable question must not be answered "no".
 */
export function shouldRenderStageData(availability: StageAvailability): boolean {
  return availability !== 'not-reached'
}
