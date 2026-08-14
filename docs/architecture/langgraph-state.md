# LangGraph State Contracts

Three LangGraph workflows exist, one per component that does multi-step
reasoning (see [service-boundaries.md](service-boundaries.md)). Each has a
single typed state (a `TypedDict`, per LangGraph convention — not an
arbitrary dict) that no other workflow shares. State types live under
`workflows/langgraph/<workflow_name>/state.py` (see
[repository-structure.md](repository-structure.md)).

A LangGraph workflow always processes exactly **one unit of work** end to
end, in-process — one job, one contact-search request, one outreach draft.
Kafka never sits between two nodes of the same workflow (see
[overview.md](overview.md), rule 2). Two different agents implementing two
different nodes of the *same* workflow must agree only on this state
shape — nothing else.

---

## JobMatchingState

Owned by Job Matching Service.

```python
class JobMatchingState(TypedDict):
    # input, set once at graph entry, never mutated by nodes
    job: NormalizedJob
    profiles: list[ResumeProfile]
    preferences: UserPreferences

    # working state, populated by nodes
    profile_scores: list[ProfileMatchScore]
    selected_profile: ResumeProfile | None
    selected_resume_id: ResumeId | None
    recommendation: MatchRecommendation | None

    # output, set by the final node
    final_match: JobMatchResult | None

    errors: list[WorkflowError]
```

### Graph

```
load_profiles ──► score_profile (fan-out, one per profile) ──► select_best_profile
                                                                       │
                                                                       ▼
                                                          compute_recommendation
                                                                       │
                                                                       ▼
                                                        persist_and_publish
```

### Nodes

**`load_profiles`**
```
Input State Fields: job.user_id
External Tools Used: Resume/Profile Service API (GET /profiles)
Output State Fields: profiles
Possible Routes: -> score_profile (always); -> persist_and_publish directly
                  if profiles is empty (short-circuit)
Possible Errors: NO_PROFILES_AVAILABLE (routes to persist_and_publish with
                  an entry in errors — see persist_and_publish below for
                  this path's exact terminal shape, ratified after Wave 2's
                  Job Matching implementation)
```

**`score_profile`** (conceptually one scoring pass per profile in `profiles`)
```
Input State Fields: job, all profiles from profiles
External Tools Used: LLM Provider Layer (semantic scoring)
Output State Fields: profile_scores (one ProfileMatchScore per profile)
Possible Routes: -> select_best_profile (after every profile is scored)
Possible Errors: LLM_PROVIDER_ERROR (that profile's score is recorded with
                  score=0.0 and an errors entry; does not abort the whole run
                  unless every profile fails, which then routes to
                  persist_and_publish with recommendation=IGNORE)
```

**Fan-out, ratified as intentionally deferred (not a defect).** This node
was originally described as "invoked once per profile, fanned out" (i.e.
one LangGraph node instance per profile, run concurrently via `Send`).
Wave 2's Job Matching implementation found this unsafe against the current
state contract: `profile_scores` above is a plain `list[ProfileMatchScore]`
with no `Annotated[..., reducer]`, and LangGraph requires a
reducer-annotated field to safely merge concurrent partial-state updates
from multiple `Send`-routed branches writing to the same key — confirmed
empirically to raise `InvalidUpdateError: Can receive only one value per
step` otherwise. The implemented shape is therefore a single node that
scores every profile in `state["profiles"]` sequentially within one
invocation (still recording each profile's `LLM_PROVIDER_ERROR`
independently, per the error contract above — the per-profile error
isolation is unchanged, only the concurrency is).

This is recorded here as a deliberate, documented deferral, not a
blocker: adding a reducer (e.g. `Annotated[list[ProfileMatchScore],
operator.add]`) to `profile_scores` and switching to a `Send`-based
fan-out is a valid future optimization for jobs with many candidate
profiles, but changes this locked state contract's shape and is out of
scope for the increment that first implemented this workflow. Revisit only
if per-job matching latency (with LLM calls now serialized across all of a
user's profiles) is measured to actually matter — do not parallelize
speculatively.

**`select_best_profile`**
```
Input State Fields: profile_scores
Output State Fields: selected_profile, selected_resume_id
External Tools Used: none (pure selection over profile_scores)
Possible Routes: -> compute_recommendation (always)
```

**`compute_recommendation`**
```
Input State Fields: selected_profile's score, job
External Tools Used: none (threshold logic over the score)
Output State Fields: recommendation
Possible Routes: -> persist_and_publish (always)
```

**`persist_and_publish`**
```
Input State Fields: job, selected_profile, selected_resume_id, recommendation,
                     profile_scores
External Tools Used: job_matches repository (DB write), Kafka producer
Output State Fields: final_match
Destination: INSERT job_matches; publish JobMatchedEvent always, plus
             JobShortlistedEvent if recommendation == SHORTLIST
             (ContactsRequestedEvent deferred — see kafka-topics.md's
             "Job Matching Service event flow, current state" note)
Possible Errors: MATCHING_FAILED (DB or publish failure — the whole
                  WorkflowExecution is marked FAILED, no partial event
                  is published)
```

**`persist_and_publish`'s `NO_PROFILES_AVAILABLE` terminal path** (ratified
after Wave 2's Job Matching implementation, ownership.md's "stop and
document" pattern resolved here rather than left open): `JobMatch` and
`JobMatchResult` both require non-null `selected_profile_id` and
`selected_resume_id` (domain-model.md#jobmatch, shared-types.md#jobmatchresult)
— with zero `ACTIVE` profiles there is no profile to reference, so no
well-formed row or event can exist. This is not the same situation as an
ordinary `IGNORE` recommendation (which still has a selected profile, just
a low-scoring one) — `MatchRecommendation.IGNORE` is never actually used
for this path, despite `load_profiles`'s "recommendation=IGNORE" phrasing
in an earlier draft of this document. The ratified terminal shape is:

```
NO_PROFILES_AVAILABLE
      │
      ▼
Job.processing_status = FAILED     (persist_and_publish's only DB write)
      │
      ▼
no JobMatch row inserted
      │
      ▼
no jobs.matched / jobs.shortlisted event published
```

`selected_profile_id`/`selected_resume_id` are deliberately **not** made
nullable to represent this case — a `JobMatch` row's meaning is "a
successful match with a selected profile"; a job that could not be matched
at all has no match to represent, so the correct representation is the
absence of a row, not a row with null selection fields. Making those
fields nullable would weaken the type for every other (successful) case to
accommodate one failure path that already has a clean representation
without it. Downstream components learn of this outcome by its absence
(no `jobs.matched` ever arrives for this `job_id`) and, once Tracking
Service exists, by `Job.processing_status` reaching `FAILED` without a
preceding `MATCHED` — consistent with `state-machines.md`'s existing
at-least-once/no-cross-topic-ordering tolerance (`kafka-topics.md`); no new
"failure event" is introduced, since nothing today is documented to
consume one and `component-contracts.md`'s Tracking Service section
already tolerates a job never reaching `jobs.matched`.

---

## ContactDiscoveryState

Owned by Contact Discovery Service.

```python
class ContactDiscoveryState(TypedDict):
    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None

    candidates: list[ContactCandidate]
    ranked_contacts: list[RankedContact]

    errors: list[WorkflowError]
```

### Graph

```
search_contacts ──► rank_contacts ──► persist_and_publish
```

### Nodes

**`search_contacts`**
```
Input State Fields: company, title, location
External Tools Used: people-search APIs/tools, LLM Provider Layer
                      (query construction / result interpretation)
Output State Fields: candidates
Possible Routes: -> rank_contacts (always, even if candidates is empty)
Possible Errors: CONTACT_SEARCH_FAILED (candidates left empty, error recorded,
                  still routes forward so an empty ContactsFoundEvent is published)
```

**`rank_contacts`**
```
Input State Fields: candidates, job context (company, title)
External Tools Used: LLM Provider Layer (relevance signals), scoring logic
Output State Fields: ranked_contacts (ordered by relevance_score descending)
Possible Routes: -> persist_and_publish (always)
Possible Errors: LLM_PROVIDER_ERROR (falls back to rule-based scoring signals
                  only — same_company, role_similarity — rather than aborting)
```

**`persist_and_publish`**
```
Input State Fields: ranked_contacts
External Tools Used: contacts / contact_rankings repositories, Kafka producer
Output: none in state — this is the terminal node
Destination: INSERT contacts + contact_rankings (one pair per ranked contact);
             publish ContactsFoundEvent (contacts list may be empty)
Possible Errors: none beyond standard DB/publish failure (WorkflowExecution
                  marked FAILED if so)
```

---

## OutreachGenerationState

Owned by Outreach Service.

```python
class OutreachGenerationState(TypedDict):
    job_id: JobId
    user_id: UserId
    contact: RankedContact
    candidate_profile: ResumeProfile
    selected_resume_id: ResumeId

    channel: OutreachChannel | None
    draft_message: str | None

    errors: list[WorkflowError]
```

### Graph

```
select_channel ──► generate_message ──► persist_and_publish
```

### Nodes

**`select_channel`**
```
Input State Fields: contact.profile_url, contact.email, contact.contact_type
External Tools Used: none (rule-based: prefer profile_url-based channel if
                      present, else email)
Output State Fields: channel
Possible Routes: -> generate_message (always)
```

**`generate_message`**
```
Input State Fields: channel, contact, candidate_profile, job context
External Tools Used: LLM Provider Layer (message generation)
Output State Fields: draft_message
Possible Routes: -> persist_and_publish (always)
Possible Errors: OUTREACH_GENERATION_FAILED, LLM_PROVIDER_ERROR (aborts —
                  no draft is persisted or published on failure, since a
                  human cannot approve a message that doesn't exist)
```

**`persist_and_publish`**
```
Input State Fields: channel, draft_message
External Tools Used: outreach repository (DB write), Kafka producer
Output: none in state — terminal node
Destination: INSERT outreach (status=PENDING_APPROVAL); publish
             OutreachGeneratedEvent
Possible Errors: none beyond standard DB/publish failure
```

---

## What is deliberately not a LangGraph workflow

Resume parsing (Resume/Profile Service) is a single LLM extraction call plus
persistence — not modeled as a multi-node graph, since there is no
branching/routing decision involved (see
[component-contracts.md](component-contracts.md#resume-parsing-workflow-internal-background-step-not-langgraph--see-langgraph-statemd)
for its contract as a plain async background step). If parsing later grows
branching logic (e.g. profession-specific extraction passes), it becomes a
fourth typed state at that point — not before.
