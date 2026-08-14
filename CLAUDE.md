# Project Instructions

Before performing architectural or implementation work:

1. Read about_project.md.
2. Treat about_project.md as the product source of truth.
3. Do not hard-code the platform for software engineers.
4. The system must remain profession-independent.
5. Python is the primary language.
6. LangGraph owns agent reasoning/workflow orchestration.
7. Kafka owns asynchronous event distribution.
8. PostgreSQL owns persistent application state.
9. External outreach requires human approval.
10. Do not modify another component's files without a clear integration requirement.

Architecture rule:
Do not use Kafka between every LangGraph node.

Kafka is for communication between independently scalable components.
LangGraph is for reasoning and workflow execution inside a component.

Before introducing a shared model, API contract, Kafka event, or database schema,
check whether an existing shared contract already exists.

Tests are required for new behavior.