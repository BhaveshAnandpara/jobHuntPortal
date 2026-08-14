"""LangGraph workflows. See docs/architecture/langgraph-state.md.

Three workflows exist, one per component that does multi-step reasoning.
Each has a single typed state (a TypedDict, per LangGraph convention) that
no other workflow shares. A LangGraph workflow always processes exactly
one unit of work end to end, in-process — Kafka never sits between two
nodes of the same workflow (docs/architecture/overview.md, rule 2).
"""
