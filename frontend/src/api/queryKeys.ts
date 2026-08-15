/**
 * The one query-key convention for the whole app — see
 * docs/frontend/state-management.md#query-key-convention. Every
 * `useQuery`/`useMutation` invalidation (written by feature agents in a
 * later wave, not this skeleton step) must build its key from here, never
 * an inline array literal, so cache invalidation across features stays
 * predictable without a central registry.
 *
 * No key carries a `userId` segment: identity now comes from the
 * authenticated request (`api/client.ts`'s `Authorization` header), not a
 * client-supplied value, and only one account is ever "logged in" per
 * browser session — `IdentityProvider`'s `setToken`/`clearToken` calls
 * `queryClient.clear()` on every login/logout, so a per-user key is no
 * longer needed to keep a second account's data from bleeding into view.
 *
 * Owner: frontend-api-agent. Feature agents consume this; they do not add
 * ad hoc keys of their own for data that already has an entry here.
 */

export const queryKeys = {
  resumes: () => ['resumes'] as const,
  resume: (resumeId: string) => ['resume', resumeId] as const,
  profiles: () => ['profiles'] as const,
  profile: (profileId: string) => ['profile', profileId] as const,
  job: (jobId: string) => ['job', jobId] as const,
  match: (jobId: string) => ['match', jobId] as const,
  contacts: (jobId: string) => ['contacts', jobId] as const,
  // `status` is an *optional tuple element*, not an always-present slot that
  // can hold `undefined` — omitting it (rather than appending a literal
  // `undefined` second element) is what makes `queryKeys.outreachList()`
  // usable as a true prefix filter in `invalidateQueries`, matching every
  // status-scoped query too. TanStack's default (non-`exact`) key matching
  // only compares the positions present in the filter key, but an explicit
  // `undefined` in a present position is still compared against the actual
  // key's value at that position and fails to match a concrete status — see
  // the reasoning in outreach.ts/tracking.ts's mutation hooks.
  outreachList: (status?: string) =>
    (status ? (['outreach', status] as const) : (['outreach'] as const)),
  outreachItem: (outreachId: string) => ['outreach-item', outreachId] as const,
  applications: (status?: string) =>
    (status ? (['applications', status] as const) : (['applications'] as const)),
  application: (applicationId: string) => ['application', applicationId] as const,
  history: (applicationId: string) => ['history', applicationId] as const,
  preferences: () => ['preferences'] as const,
} as const
