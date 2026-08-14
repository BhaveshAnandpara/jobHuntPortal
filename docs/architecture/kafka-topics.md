# Kafka Topics

Ten topics. Each one represents a real asynchronous boundary between
independently scalable components — not a hop between LangGraph nodes (see
[overview.md](overview.md#core-architectural-rules-from-claudemd-made-explicit),
rule 1-2).

## Design decisions worth knowing before adding a topic

- **`jobs.shortlisted` vs. `contacts.requested`** are deliberately both kept,
  and deliberately different: `jobs.shortlisted` is a *fact* event (many
  consumers may care that a job crossed the matching gate — currently
  Tracking, potentially future Analytics) while `contacts.requested` is a
  *command* event with exactly one intended consumer (Contact Discovery
  Service) and exactly one meaning ("go find contacts for this job now").
  Job Matching Service publishes both when it shortlists a job. The command
  topic also gives Contact Discovery Service a single input topic it can
  scale consumers against independently of who else listens to
  `jobs.shortlisted`, and it lets a manual "search again" API call reuse the
  same trigger without pretending a job was re-shortlisted.
- **No `contacts.discovered` intermediate topic.** Discovery and ranking
  happen as two LangGraph nodes inside one Contact Discovery Service run
  (see [langgraph-state.md](langgraph-state.md)), not two Kafka hops,
  because splitting them would not create an independently scalable
  boundary — ranking always needs that run's own discovered candidates, and
  about_project.md only names `contacts.requested`/`contacts.found`.
- **`profiles.updated` is an addition beyond about_project.md's example
  list**, required to satisfy the explicit product requirement that
  "previously discovered opportunities may also be re-evaluated against the
  newly added profile" (about_project.md, User Journey 1). Without it, Job
  Matching Service would have no trigger to re-run matching when a new
  resume/profile is added after jobs already exist.
- **No topic between every LangGraph node.** `jobs.matched` and
  `jobs.shortlisted` are separate topics not because matching and
  shortlisting are different LangGraph nodes (they're the same
  `JobMatchingState` run — the gate is a routing decision inside it), but
  because they're semantically different *facts* with different natural
  consumer sets (every matched job vs. only the shortlisted subset).
- **Contact Discovery Service does not consume `outreach.sent`.** It might
  seem natural for `Contact.status` to advance to a "contacted" state once
  outreach is sent, but making Contact Discovery Service consume an
  Outreach Service event alongside Outreach Service already consuming
  `contacts.found` (produced by Contact Discovery Service) would create a
  circular event dependency between those two components — see
  [dependency-graph.md](dependency-graph.md#no-circular-dependencies). Instead,
  "has this contact been reached out to" is answered by querying `Outreach`
  records for that `contact_id` (via Outreach Service's API), never stored
  redundantly on `Contact`. See
  [state-machines.md](state-machines.md#contact-lifecycle-contactstatus--contactstatus).
- **Outreach rejection has no topic.** See
  [component-contracts.md](component-contracts.md#post-outreachoutreach_idreject)
  — adding `outreach.rejected` today would have exactly zero consumers, so
  it's deferred rather than spun up speculatively.

## `profiles.updated` and `contacts.requested` — contract vs. current implementation status

Both topics are fully locked contracts (this section restates them
explicitly, per a Job Matching architecture cleanup review, so their
producer/consumer/purpose are unambiguous before Contact Discovery Service
is built) but only partially wired in code as of Job Matching Service's
Wave 2 implementation:

```
profiles.updated
    producer:  Resume/Profile Service (on CandidateProfile create/archive —
               see component-contracts.md#resumeprofile-service)
    consumer:  Job Matching Service (re-matching path)
    purpose:   trigger re-evaluation of a user's still-open opportunities
               (jobs not yet SHORTLISTED) when their profile data materially
               changes — either a new profile becomes available or an
               existing one is archived — so a previously-IGNORED or
               BORDERLINE job gets a chance to be re-scored against the
               user's current profile set, per about_project.md's "Previously
               discovered opportunities may also be re-evaluated against the
               newly added profile" (User Journey 1)
    payload:   EventEnvelope[ProfileUpdateSummary] — event-contracts.md
    status:    contract locked; Job Matching Service's consumer
               (`handle_profile_updated`) is an explicit, documented stub —
               not wired to any running EventConsumer. Deferred because the
               re-matching path's own scope (which jobs count as "still
               open", how a re-match's new JobMatch row relates to the
               original one) is a distinct increment from the primary
               jobs.discovered path Job Matching Service's Wave 2
               implementation focused on — see
               component-contracts.md#job-matching-service's profiles.updated
               consumer entry for the full already-locked contract this
               stub must eventually satisfy.

contacts.requested
    producer:  Job Matching Service, from persist_and_publish, only when
               recommendation == SHORTLIST (published alongside
               jobs.shortlisted, same triggering condition — see
               langgraph-state.md#jobmatchingstate); also Contact Discovery
               Service itself, as a passthrough for its own
               POST /jobs/{job_id}/contacts/search manual re-trigger
    consumer:  Contact Discovery Service (its only consumer — this is a
               command topic, not a fact topic, see the design-decision bullet
               above)
    purpose:   command Contact Discovery Service to search for and rank
               contacts for a shortlisted job, carrying exactly what it
               needs (job_id, user_id, company, title, location) with no
               callback required
    payload:   EventEnvelope[ContactSearchRequest] — event-contracts.md
    status:    contract locked; Job Matching Service's producer
               (`publish_contacts_requested`) is an explicit, documented stub
               — not called from persist_and_publish. Deferred because
               Contact Discovery Service does not exist yet; publishing a
               command event with zero consumers would be premature (and
               would need to be re-validated against whatever Contact
               Discovery Service's Wave 2 implementation actually needs)
               rather than genuinely useful today.
```

Both stubs are additive, mechanical work once their consuming side exists
or their own increment is scheduled — `publish_contacts_requested` needs
only to be called from `persist_and_publish` alongside the existing
`publish_job_shortlisted` call (same `JobMatchResult` → `ContactSearchRequest`
projection already implied by the locked payload shape above); no new
architecture decision is required to complete either.

## Delivery semantics (applies to all topics)

- **At-least-once delivery.** Every consumer must be idempotent — safe to
  process the same event twice. Practically: Tracking Service's upserts key
  off `job_id`/`outreach_id` and only apply forward-progressing status
  changes (see [state-machines.md](state-machines.md)); other consumers key
  their writes off the natural entity id in the payload.
- **No cross-topic ordering guarantee.** `jobs.matched` for a job may be
  observed by Tracking before or after `jobs.discovered` for the same job
  under retry/rebalance scenarios. Consumers that build cumulative state
  (Tracking) must tolerate out-of-order arrival — never regress a status,
  and create the `Application` row on whichever event arrives first if
  `jobs.discovered` hasn't been seen yet.
- **Ordering within a topic** is guaranteed only within a partition. Every
  topic below is keyed so that all events about the same job (or outreach)
  land in the same partition, giving in-order delivery for that entity's own
  event history within a single topic.
- **Retry strategy:** consumer-level retry with exponential backoff (3
  attempts) on transient failures (LLM timeouts, external API errors,
  transient DB errors). After exhausting retries, the message is published
  to the topic's dead-letter topic (`<topic>.dlq`) with the failure reason
  attached in `EventMetadata` (`EventMetadata.failure_reason`, and
  `EventMetadata.error_code` when the failure maps to a known `ErrorCode` —
  both fields are `None` on every non-DLQ message), and the consumer commits
  its offset so the partition is not blocked. DLQ messages require
  manual/operator reprocessing — no automatic DLQ replay is implemented.
- **Malformed/undeserializable messages — the one exception to the above.**
  A message that cannot be parsed back into a valid `EventEnvelope[T]` at
  all (corrupt bytes, an incompatible shape) is a *permanent* failure, not
  a transient one — it will not become parseable on retry. Such a message
  skips the 3-attempt retry entirely and is republished to `<topic>.dlq`
  immediately, with two differences from the normal DLQ path above, both
  driven by the same underlying fact: the bytes never formed a valid
  envelope, so there is no `EventEnvelope`/`EventMetadata` to attach
  anything to:
    - the DLQ message **body** is the original bytes, preserved verbatim
      (not re-wrapped, not re-shaped — an operator inspecting the DLQ sees
      exactly what was received);
    - the failure reason travels as a **Kafka message header** instead of
      in `EventMetadata.failure_reason` — reusing the existing
      producer/consumer header mechanism (`infrastructure/kafka/client.py`'s
      `Headers`, already used by `EventProducer.publish_raw`) rather than
      introducing a new wrapper type or envelope variant. The header key is
      `failure_reason` (UTF-8 bytes, truncated to 2000 bytes); no
      `error_code` header is set, since a raw deserialization failure isn't
      reliably classifiable into a shared `ErrorCode`.

  This is deliberately the simplest fix that preserves the existing event
  architecture: no new DLQ wrapper type, no schema change to
  `EventEnvelope`/`EventMetadata` (`event-contracts.md`) — the two DLQ
  message shapes (envelope-with-populated-metadata vs. raw-bytes-with-header)
  are distinguished by trying to parse the body, exactly the same way a
  consumer distinguishes them on the primary topic.
- **Consumer groups:** one primary consumer group per topic per consuming
  component (named `{component-name}`), so scaling workers is scaling
  consumers within that group. Multiple independent consumer groups on the
  same topic are expected (e.g. Tracking Service and a future Analytics
  service both consuming `jobs.matched` independently) — this is exactly
  what Kafka pub/sub is for and requires no coordination between the two
  consumer groups.

## Topics

### `jobs.discovered`

| | |
|---|---|
| Purpose | A job posting has been ingested (manually or automatically) and normalized. |
| Producers | Job Ingestion Service, Job Discovery Service |
| Consumers | Job Matching Service, Tracking Service |
| Event type | `JobDiscoveredEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `jobs.discovered.dlq` |
| Multiple consumer groups | yes (Job Matching, Tracking are independent groups today) |

### `jobs.matched`

| | |
|---|---|
| Purpose | Job Matching Service finished evaluating a job against the user's profiles — published for every job regardless of recommendation. |
| Producers | Job Matching Service |
| Consumers | Tracking Service |
| Event type | `JobMatchedEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `jobs.matched.dlq` |
| Multiple consumer groups | reserved for future Analytics |

### `jobs.shortlisted`

| | |
|---|---|
| Purpose | A job cleared the matching gate (`recommendation == SHORTLIST`). |
| Producers | Job Matching Service |
| Consumers | Tracking Service |
| Event type | `JobShortlistedEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `jobs.shortlisted.dlq` |
| Multiple consumer groups | reserved for future Analytics |

### `profiles.updated`

| | |
|---|---|
| Purpose | A `CandidateProfile` was created or archived; downstream matching may need to react. |
| Producers | Resume/Profile Service |
| Consumers | Job Matching Service |
| Event type | `ProfileUpdatedEvent` |
| Partition key | `user_id` |
| Ordering | per-`user_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `profiles.updated.dlq` |
| Multiple consumer groups | no (single known consumer today) |

### `contacts.requested`

| | |
|---|---|
| Purpose | Command: find and rank contacts for a shortlisted (or manually re-requested) job. |
| Producers | Job Matching Service (automatic, on shortlist), Contact Discovery Service (its own `POST /jobs/{job_id}/contacts/search` publishes here, not a self-call) |
| Consumers | Contact Discovery Service |
| Event type | `ContactsRequestedEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `contacts.requested.dlq` |
| Multiple consumer groups | no |

### `contacts.found`

| | |
|---|---|
| Purpose | Contact discovery + ranking completed for a job (possibly with zero results). |
| Producers | Contact Discovery Service |
| Consumers | Outreach Service, Tracking Service |
| Event type | `ContactsFoundEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `contacts.found.dlq` |
| Multiple consumer groups | yes (Outreach, Tracking) |

### `outreach.generated`

| | |
|---|---|
| Purpose | A draft outreach message was generated and is awaiting human approval. |
| Producers | Outreach Service |
| Consumers | Tracking Service |
| Event type | `OutreachGeneratedEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `outreach.generated.dlq` |
| Multiple consumer groups | no |

### `outreach.approved`

| | |
|---|---|
| Purpose | The user approved (optionally edited) a draft; ready to send. |
| Producers | Outreach Service (published from the approval API handler) |
| Consumers | Outreach Service (dedicated send-worker consumer group, decoupled from the API request), Tracking Service |
| Event type | `OutreachApprovedEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `outreach.approved.dlq` |
| Multiple consumer groups | yes (send-worker, Tracking) |

### `outreach.sent`

| | |
|---|---|
| Purpose | Outreach was successfully delivered via its channel's external provider. |
| Producers | Outreach Service (send worker) |
| Consumers | Tracking Service |
| Event type | `OutreachSentEvent` |
| Partition key | `job_id` |
| Ordering | per-`job_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `outreach.sent.dlq` |
| Multiple consumer groups | no |

### `applications.updated`

| | |
|---|---|
| Purpose | The canonical `Application.status` changed, from any cause. |
| Producers | Tracking Service |
| Consumers | reserved for future Analytics/notifications; none required today |
| Event type | `ApplicationUpdatedEvent` |
| Partition key | `application_id` |
| Ordering | per-`application_id` only |
| Delivery | at-least-once |
| Retry / DLQ | 3x backoff, then `applications.updated.dlq` |
| Multiple consumer groups | yes, by design (this is the platform's general-purpose "opportunity state changed" fan-out point) |
