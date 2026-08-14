# Architecture Overview

## Status

This is the **contract phase**. Nothing in this directory implements business
logic. Every document here exists so that independent agents can build
components in parallel without inventing incompatible types, topics, tables,
or state machines.

If you are about to write code and cannot answer where your input comes from,
what shape it is, or where your output goes, stop and find the answer in these
documents before inventing one.

## Product source of truth vs. engineering source of truth

- `about_project.md` (repo root) defines *what* the product does and *why*.
  It is authoritative for product behavior, user journeys, and the intended
  event flow.
- `CLAUDE.md` (repo root) defines engineering constraints (Python, LangGraph
  for reasoning, Kafka for async distribution, Postgres for state, human
  approval for outreach, no hard-coded profession bias).
- `docs/architecture/*` (this directory) is authoritative for **shared
  contracts**: exact types, exact topics, exact table ownership, exact APIs.
  When about_project.md describes something loosely (e.g. "the matching
  stage acts as a gate"), the documents here make it precise and binding.

If a future change conflicts with about_project.md's product intent, the
product doc wins and this architecture must be updated. If a future change
conflicts with a *shared contract* defined here, the contract wins until it is
formally revised (see [versioning-rules](#versioning-rules) below) — no
component may silently diverge from it.

## Deployment model

The system is a **modular monolith with logically independent
components**, not a fleet of separately-deployed microservices with separate
databases. This matches about_project.md's technology table (one PostgreSQL
instance, one Kafka cluster) and CLAUDE.md's instruction to avoid
over-engineering.

Consequences:

- There is **one PostgreSQL database**. Table *ownership* is enforced by
  convention and code review, not by network isolation: only the owning
  component's repository/data-access code may write to its tables. Other
  components read through an API or a published event — never through a
  direct cross-component query against another component's tables, with the
  single documented exception in [database-ownership.md](database-ownership.md#shared-observability-table).
- Components can be split into separately-deployed services later without
  changing any contract in this directory, because the contracts (types,
  topics, table ownership, APIs) are already drawn as if they were separate
  services.
- Kafka is real infrastructure (not in-process), because it is the mechanism
  for parallel worker scaling described in about_project.md.

## The five layers

Every shared type in this system belongs to exactly one of these layers. A
type never silently changes shape while staying in the same layer — moving
between layers is always an explicit, named transformation (see
[shared-types.md](shared-types.md#transformation-rules)).

| Layer | Purpose | Naming | Defined in |
|---|---|---|---|
| API types | HTTP request/response contracts | `*Request` / `*Response` | [api-contracts.md](api-contracts.md) |
| Domain types | Canonical in-process representation of a concept | bare noun, e.g. `NormalizedJob` | [domain-model.md](domain-model.md), [shared-types.md](shared-types.md) |
| Event payload types | What travels on Kafka, wrapped in `EventEnvelope[T]` | `*Event` | [event-contracts.md](event-contracts.md) |
| Database record types | SQLAlchemy models, one per owned table | `*Record` | [database-ownership.md](database-ownership.md) |
| LangGraph state types | Typed state for a single workflow run | `*State` | [langgraph-state.md](langgraph-state.md) |

## Core architectural rules (from CLAUDE.md, made explicit)

1. **Kafka is for communication between independently scalable
   components.** It marks a boundary where work should be distributable
   across parallel workers or where a producer and consumer must not be
   temporally coupled (the consumer may be down, slow, or scaled
   independently).
2. **LangGraph is for reasoning and workflow execution inside a
   component.** A single LangGraph run always processes one unit of work
   (one job, one contact-search request, one outreach draft) end-to-end
   in-process. LangGraph nodes call each other directly through graph edges,
   never through Kafka.
3. A component only writes to tables it owns. See
   [ownership.md](ownership.md) and [database-ownership.md](database-ownership.md).
4. Cross-component communication uses exactly one of: a documented API call,
   a documented Kafka event, or a shared type. Never a direct import of
   another component's internal module, and never a direct table write.
5. The platform is profession-independent. No shared type, enum, or table
   may encode assumptions specific to software engineering or any other
   single profession. Where about_project.md gives software-engineering
   examples (e.g. contact types), the corresponding shared type generalizes
   them (see [shared-enums](shared-types.md#shared-enums)).
6. External outreach requires human approval — this is modeled as a real
   state transition (`OUTREACH_GENERATED → OUTREACH_APPROVED`) owned by the
   Outreach Service, not a side effect embedded in another component.

## Document index

| Document | Answers |
|---|---|
| [domain-model.md](domain-model.md) | What are the core entities and their fields? |
| [shared-types.md](shared-types.md) | What are the canonical IDs, DTOs, and transformation rules? |
| [ownership.md](ownership.md) | Which component owns which entity, table, topic, API? |
| [component-contracts.md](component-contracts.md) | For each component: exact input, output, trigger, errors |
| [kafka-topics.md](kafka-topics.md) | What topics exist, who produces/consumes, delivery semantics |
| [event-contracts.md](event-contracts.md) | The event envelope and every event payload type |
| [database-ownership.md](database-ownership.md) | Every table, its owner, and its access rules |
| [service-boundaries.md](service-boundaries.md) | The logical services and what's outside their responsibility |
| [api-contracts.md](api-contracts.md) | Every HTTP endpoint's request/response contract |
| [state-machines.md](state-machines.md) | Valid lifecycle transitions for Application, Resume, Outreach, Contact, Workflow |
| [langgraph-state.md](langgraph-state.md) | Typed state per workflow, per-node input/output |
| [dependency-graph.md](dependency-graph.md) | Which components may depend on which, by dependency type |
| [repository-structure.md](repository-structure.md) | Where code for each boundary lives |

## Versioning rules

See [shared-types.md#versioning](shared-types.md#versioning-rules) for the
full policy. Summary: event payloads carry `event_version`; additive changes
(new optional fields) don't bump it; breaking changes require a new version
and a deprecation window where the producer may dual-write.

## How to use this as an implementing agent

1. Find your component in [ownership.md](ownership.md) and
   [service-boundaries.md](service-boundaries.md).
2. Read your component's entry in [component-contracts.md](component-contracts.md)
   end to end before writing code.
3. Only import types from `shared/` for anything crossing a component
   boundary. Never redefine a type that already exists in
   [shared-types.md](shared-types.md) or [domain-model.md](domain-model.md).
4. If you believe you need a new shared type, event, topic, or table, that is
   an architecture change — flag it rather than inventing a local
   equivalent (per CLAUDE.md: "check whether an existing shared contract
   already exists").
