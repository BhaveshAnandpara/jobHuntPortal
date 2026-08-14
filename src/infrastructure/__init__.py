"""Infrastructure layer — not business components, but shared plumbing
every component depends on. See
docs/architecture/service-boundaries.md#kafka-infrastructure and
#llm-provider-layer, and docs/architecture/dependency-graph.md#5-llmtool-dependencies.

No module under infrastructure/ may contain business decisions about what
an event/prompt/table row means — that belongs to the owning component.
"""
