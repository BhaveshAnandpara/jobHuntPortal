"""Graph assembly for the Contact Discovery LangGraph workflow.
See docs/architecture/langgraph-state.md#contactdiscoverystate for the
graph diagram. Wires the node functions in
workflows/langgraph/contact_discovery/nodes.py into a compiled LangGraph
StateGraph over ContactDiscoveryState.

Graph: search_contacts -> rank_contacts -> persist_and_publish, all
in-process, no Kafka hop between nodes — every edge is unconditional
because every node in this workflow always routes forward regardless of
error (kafka-topics.md's "No contacts.discovered intermediate topic"
design decision; langgraph-state.md#contactdiscoverystate's per-node
"Possible Routes" entries, all of which read "always").

Owned by Contact Discovery Service.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from workflows.langgraph.contact_discovery.nodes import (
    persist_and_publish,
    rank_contacts,
    search_contacts,
)
from workflows.langgraph.contact_discovery.state import ContactDiscoveryState


def build_graph():
    """Compile the ContactDiscoveryState graph per
    docs/architecture/langgraph-state.md#contactdiscoverystate:

        search_contacts -> rank_contacts -> persist_and_publish
    """
    graph = StateGraph(ContactDiscoveryState)
    graph.add_node("search_contacts", search_contacts)
    graph.add_node("rank_contacts", rank_contacts)
    graph.add_node("persist_and_publish", persist_and_publish)

    graph.add_edge(START, "search_contacts")
    graph.add_edge("search_contacts", "rank_contacts")
    graph.add_edge("rank_contacts", "persist_and_publish")
    graph.add_edge("persist_and_publish", END)

    return graph.compile()


__all__ = ["build_graph"]
