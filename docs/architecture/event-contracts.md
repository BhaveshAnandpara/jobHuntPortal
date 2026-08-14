# Event Payload Contracts

Every Kafka message is `EventEnvelope[T]` where `T` is one of the payload
types below. Components never publish or consume a bare dict — the Kafka
producer/consumer wrapper (in `infrastructure/kafka/`) serializes/deserializes
directly to/from these Pydantic types.

## EventEnvelope[T]

```
EventEnvelope[T]
    event_id: EventId
    event_type: EventType
    event_version: str            # e.g. "1.0" — see shared-types.md#versioning-rules
    timestamp: datetime            # UTC, event creation time
    correlation_id: CorrelationId  # propagated unchanged across a causal chain
    user_id: UserId
    payload: T
    metadata: EventMetadata

EventMetadata
    producer: str                  # component name, e.g. "job-matching-service"
    retry_count: int                # default 0, incremented by the retry wrapper
    source: str | None              # e.g. "manual" | "automatic", where applicable
    failure_reason: str | None      # set only on DLQ republish (added, additive — see kafka-topics.md)
    error_code: ErrorCode | None    # set only on DLQ republish, when classifiable (additive)
```

`failure_reason`/`error_code` above are only ever populated on a message
that *was* a valid `EventEnvelope[T]` before it landed on `<topic>.dlq`. A
message that fails deserialization was never a valid envelope in the first
place, so it has no `EventMetadata` to populate — see
[kafka-topics.md](kafka-topics.md)'s "Malformed/undeserializable messages"
note for that case's DLQ shape (raw bytes + a Kafka header, not this
struct).

`event_type` must match the payload type per the table below — the
serializer validates this at publish time so a producer can't accidentally
publish a `JobMatchedEvent` payload tagged as `JOB_DISCOVERED`.

## Payload types

### JobDiscoveredEvent

```
payload: NormalizedJob   (defined in shared-types.md#normalizedjob)
```

| Producer | Job Ingestion Service, Job Discovery Service |
|---|---|
| Topic | `jobs.discovered` |
| Consumer input contract | identical `NormalizedJob` shape — no consumer-side transformation before use |
| Required fields | `job_id`, `user_id`, `company`, `title`, `description`, `source_type`, `discovered_at` |
| Optional fields | `location`, `extracted_skills`, `experience_required`, `source_url` |

### ProfileUpdatedEvent

```
payload: ProfileUpdateSummary
    profile_id: ProfileId
    user_id: UserId
    resume_id: ResumeId
    change_type: ProfileChangeType   # CREATED | ARCHIVED
    updated_at: datetime
```

| Producer | Resume/Profile Service |
|---|---|
| Topic | `profiles.updated` |
| Consumer input contract | identical shape |
| Required fields | all fields above |
| Optional fields | none |

### JobMatchedEvent

```
payload: JobMatchResult   (defined in shared-types.md#jobmatchresult)
```

| Producer | Job Matching Service |
|---|---|
| Topic | `jobs.matched` |
| Consumer input contract | identical `JobMatchResult` shape |
| Required fields | `job_match_id`, `job_id`, `user_id`, `selected_profile_id`, `selected_resume_id`, `match_score`, `recommendation`, `matched_at` |
| Optional fields | `matched_skills`, `missing_skills` (may be empty lists, never absent) |

### JobShortlistedEvent

```
payload: JobMatchResult   (same type as JobMatchedEvent — see note below)
```

| Producer | Job Matching Service |
|---|---|
| Topic | `jobs.shortlisted` |
| Consumer input contract | identical `JobMatchResult` shape |
| Required fields | same as `JobMatchedEvent`, plus `recommendation` is always `SHORTLIST` |

`JobShortlistedEvent` intentionally reuses the `JobMatchResult` payload type
rather than defining a slimmer "shortlisted" type — the data is identical;
only the topic and the guaranteed value of `recommendation` differ. Defining
a second near-identical type would violate the "no duplicate or slightly
different versions of the same concept" rule.

### ContactsRequestedEvent

```
payload: ContactSearchRequest
    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None
```

| Producer | Job Matching Service (auto), Contact Discovery Service (manual re-trigger passthrough) |
|---|---|
| Topic | `contacts.requested` |
| Consumer input contract | identical shape |
| Required fields | `job_id`, `user_id`, `company`, `title` |
| Optional fields | `location` |

### ContactsFoundEvent

```
payload: ContactRankingResult   (defined in shared-types.md#contactrankingresult)
```

| Producer | Contact Discovery Service |
|---|---|
| Topic | `contacts.found` |
| Consumer input contract | identical `ContactRankingResult` shape |
| Required fields | `job_id`, `user_id`, `contacts` (list, may be empty), `ranked_at` |
| Optional fields | none — `contacts` being `[]` is the documented "no contacts found" signal, not an optional/absent field |

### OutreachGeneratedEvent

```
payload: OutreachDraft   (defined in shared-types.md#outreachdraft)
```

| Producer | Outreach Service |
|---|---|
| Topic | `outreach.generated` |
| Consumer input contract | identical `OutreachDraft` shape |
| Required fields | `outreach_id`, `job_id`, `contact_id`, `user_id`, `channel`, `draft_message`, `generated_at` |
| Optional fields | none |

### OutreachApprovedEvent

```
payload: OutreachDecision
    outreach_id: OutreachId
    job_id: JobId
    user_id: UserId
    decision: OutreachDecisionType   # APPROVED | REJECTED
    final_message: str | None         # set only if decision == APPROVED and user edited the draft
    decided_at: datetime
    decided_by: UserId
```

| Producer | Outreach Service |
|---|---|
| Topic | `outreach.approved` |
| Consumer input contract | identical shape |
| Required fields | `outreach_id`, `job_id`, `user_id`, `decision`, `decided_at`, `decided_by` |
| Optional fields | `final_message` |

Note: despite the topic name, this event's payload can in principle carry
`decision == REJECTED` if a future revision routes rejections through this
same topic; today, per
[component-contracts.md](component-contracts.md#post-outreachoutreach_idreject),
rejection does not publish anything, so in the current system every message
on `outreach.approved` has `decision == APPROVED`. The field exists now so
that adding rejection notifications later is additive, not a breaking
change.

### OutreachSentEvent

```
payload: OutreachSentConfirmation
    outreach_id: OutreachId
    job_id: JobId
    user_id: UserId
    channel: OutreachChannel
    sent_at: datetime
    external_message_id: str | None
```

| Producer | Outreach Service (send worker) |
|---|---|
| Topic | `outreach.sent` |
| Consumer input contract | identical shape |
| Required fields | `outreach_id`, `job_id`, `user_id`, `channel`, `sent_at` |
| Optional fields | `external_message_id` |

### ApplicationUpdatedEvent

```
payload: ApplicationStatusUpdate   (defined in shared-types.md#applicationstatusupdate)
```

| Producer | Tracking Service |
|---|---|
| Topic | `applications.updated` |
| Consumer input contract | identical `ApplicationStatusUpdate` shape |
| Required fields | `application_id`, `job_id`, `user_id`, `new_status`, `changed_at`, `triggered_by` |
| Optional fields | `previous_status` (null only for the very first status) |

## EventType enum members

One member per payload type above, used in `EventEnvelope.event_type`:

```
JOB_DISCOVERED
PROFILE_UPDATED
JOB_MATCHED
JOB_SHORTLISTED
CONTACTS_REQUESTED
CONTACTS_FOUND
OUTREACH_GENERATED
OUTREACH_APPROVED
OUTREACH_SENT
APPLICATION_UPDATED
```

Adding a new event type means adding one member here, one payload type in
this document, and one topic entry in
[kafka-topics.md](kafka-topics.md) — never repurposing an existing
`event_type` value for a differently-shaped payload.
