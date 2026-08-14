/**
 * The one query-key convention for the whole app — see
 * docs/frontend/state-management.md#query-key-convention. Every
 * `useQuery`/`useMutation` invalidation (written by feature agents in a
 * later wave, not this skeleton step) must build its key from here, never
 * an inline array literal, so cache invalidation across features stays
 * predictable without a central registry.
 *
 * Owner: frontend-api-agent. Feature agents consume this; they do not add
 * ad hoc keys of their own for data that already has an entry here.
 */

export const queryKeys = {
  resumes: (userId: string) => ['resumes', userId] as const,
  resume: (resumeId: string) => ['resume', resumeId] as const,
  profiles: (userId: string) => ['profiles', userId] as const,
  profile: (profileId: string) => ['profile', profileId] as const,
  job: (jobId: string) => ['job', jobId] as const,
  match: (jobId: string) => ['match', jobId] as const,
  contacts: (jobId: string) => ['contacts', jobId] as const,
  // `status` is an *optional tuple element*, not an always-present slot that
  // can hold `undefined` — omitting it (rather than appending a literal
  // `undefined` third element) is what makes `queryKeys.outreachList(userId)`
  // usable as a true prefix filter in `invalidateQueries`, matching every
  // status-scoped query for that user too. TanStack's default (non-`exact`)
  // key matching only compares the positions present in the filter key, but
  // an explicit `undefined` in a present position is still compared against
  // the actual key's value at that position and fails to match a concrete
  // status — see the reasoning in outreach.ts/tracking.ts's mutation hooks.
  outreachList: (userId: string, status?: string) =>
    (status ? (['outreach', userId, status] as const) : (['outreach', userId] as const)),
  outreachItem: (outreachId: string) => ['outreach-item', outreachId] as const,
  applications: (userId: string, status?: string) =>
    (status ? (['applications', userId, status] as const) : (['applications', userId] as const)),
  application: (applicationId: string) => ['application', applicationId] as const,
  history: (applicationId: string) => ['history', applicationId] as const,
  preferences: (userId: string) => ['preferences', userId] as const,
} as const
