"""Graph assembly for the Outreach Generation LangGraph workflow.
See docs/architecture/langgraph-state.md#outreachgenerationstate for the
graph diagram. Wires the node functions in
workflows/langgraph/outreach_generation/nodes.py into a compiled LangGraph
StateGraph over OutreachGenerationState.

Owned by Outreach Service.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from workflows.langgraph.outreach_generation.nodes import (
    generate_message,
    persist_and_publish,
    select_channel,
)
from workflows.langgraph.outreach_generation.state import OutreachGenerationState


def build_graph():
    """Compile the OutreachGenerationState graph per
    docs/architecture/langgraph-state.md#outreachgenerationstate:

        select_channel -> generate_message -> persist_and_publish

    Every edge is unconditional. `select_channel` and `generate_message`
    each abort the whole run by raising `OutreachError` on failure (see
    nodes.py) rather than routing to a different node — there is no
    partial/degraded path in this workflow (unlike Job Matching's/Contact
    Discovery's per-item-continue pattern), since a human cannot approve a
    message that was never generated.
    """
    graph = StateGraph(OutreachGenerationState)
    graph.add_node("select_channel", select_channel)
    graph.add_node("generate_message", generate_message)
    graph.add_node("persist_and_publish", persist_and_publish)

    graph.add_edge(START, "select_channel")
    graph.add_edge("select_channel", "generate_message")
    graph.add_edge("generate_message", "persist_and_publish")
    graph.add_edge("persist_and_publish", END)

    return graph.compile()


__all__ = ["build_graph"]
