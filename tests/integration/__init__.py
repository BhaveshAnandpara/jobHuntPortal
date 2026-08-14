"""Cross-component integration tests — end-to-end Kafka-flow tests that
span more than one component's boundary (e.g. jobs.discovered ->
jobs.matched -> jobs.shortlisted -> contacts.requested). Owned by the
integration agent (.claude/agents/integration-agent.md). See
docs/architecture/dependency-graph.md for the required validation flows.
"""
