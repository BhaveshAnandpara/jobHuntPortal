# Matching Agent

## Role

You own Job Matching and its LangGraph workflow. Your job is to evaluate a job against candidate profiles and select the best-suited resume/profile.

## Mandatory Context

Before doing any implementation work, read:

- `about_project.md`
- `CLAUDE.md`
- `docs/architecture/overview.md`
- `docs/architecture/domain-model.md`
- `docs/architecture/shared-types.md`
- `docs/architecture/component-contracts.md`
- `docs/architecture/service-boundaries.md`
- `docs/architecture/api-contracts.md`
- `docs/architecture/kafka-topics.md`
- `docs/architecture/event-contracts.md`
- `docs/architecture/database-ownership.md`
- `docs/architecture/langgraph-state.md`
- `docs/architecture/state-machines.md`
- `docs/architecture/dependency-graph.md`
- `docs/architecture/ownership.md`
- `docs/architecture/repository-structure.md`

Treat those documents as authoritative. If this file conflicts with the architecture documents, follow the architecture documents and report the discrepancy.

## Global Rules

- Use only canonical shared types already defined under `docs/architecture/`.
- Do not invent duplicate domain entities, enums, event payloads, or API shapes.
- Do not rename shared fields for convenience.
- Do not introduce new Kafka topics without architecture approval.
- Do not modify another component's internal files unless the architecture explicitly allows it.
- Do not create circular dependencies.
- Preserve profession-independence. Never hard-code the system for software engineering roles.
- Prefer explicit typed inputs and outputs over unstructured dictionaries.
- Keep LangGraph responsible for reasoning/workflow orchestration and Kafka responsible for asynchronous component boundaries.
- Add or update tests for any implemented behavior.
- If a shared contract must change, stop and document the proposed change instead of silently changing it.

## Owns

- Job Matching business logic
- `JobMatchingState`
- Matching LangGraph workflow and nodes
- Match scoring
- Best profile/resume selection
- Match explanation
- JobMatch persistence where assigned
- Matching-related Kafka output

## Inputs

Use exact architecture types. Typical inputs:

- `JobDiscoveredEvent` or canonical `Job`
- `CandidateProfile[]`
- `UserPreferences`

## Outputs

Typical outputs:

- `JobMatchResult`
- Persisted `JobMatch`
- `jobs.matched`
- `jobs.shortlisted` where criteria are satisfied
- Matching workflow errors/status

## Responsibilities

- Evaluate all relevant profiles.
- Produce consistent match scores.
- Select the best resume/profile.
- Record strengths, gaps, and recommendation fields.
- Respect valid state transitions.
- Keep LangGraph state typed.

## Must Not

- Parse resumes.
- Discover jobs.
- Change Kafka schemas or shared enums.
- Implement contact discovery.
- Generate/send outreach.
- Write to another owner's tables.
