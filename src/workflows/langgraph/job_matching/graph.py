"""Graph assembly for the Job Matching LangGraph workflow.
See docs/architecture/langgraph-state.md#jobmatchingstate for the graph
diagram. Wires the node functions in workflows/langgraph/job_matching/nodes.py
into a compiled LangGraph StateGraph over JobMatchingState.

Owned by Job Matching Service.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from workflows.langgraph.job_matching.nodes import (
    compute_recommendation,
    load_profiles,
    persist_and_publish,
    score_profile,
    select_best_profile,
)
from workflows.langgraph.job_matching.state import JobMatchingState


def _route_after_load_profiles(state: JobMatchingState) -> str:
    """load_profiles short-circuits straight to persist_and_publish when
    the user has no ACTIVE candidate profiles (NO_PROFILES_AVAILABLE — see
    nodes.load_profiles and langgraph-state.md#jobmatchingstate)."""
    return "score_profile" if state["profiles"] else "persist_and_publish"


def build_graph():
    """Compile the JobMatchingState graph per
    docs/architecture/langgraph-state.md#jobmatchingstate:

        load_profiles -> score_profile -> select_best_profile
                       -> compute_recommendation -> persist_and_publish

    (load_profiles short-circuits directly to persist_and_publish when
    there are no ACTIVE profiles.) See nodes.py's module docstring for why
    score_profile scores every profile within one node invocation rather
    than using LangGraph's `Send` fan-out — the locked JobMatchingState's
    `profile_scores` field has no reducer annotation, so concurrent
    `Send`-based writes to it are not safely mergeable by LangGraph.
    """
    graph = StateGraph(JobMatchingState)
    graph.add_node("load_profiles", load_profiles)
    graph.add_node("score_profile", score_profile)
    graph.add_node("select_best_profile", select_best_profile)
    graph.add_node("compute_recommendation", compute_recommendation)
    graph.add_node("persist_and_publish", persist_and_publish)

    graph.add_edge(START, "load_profiles")
    graph.add_conditional_edges(
        "load_profiles",
        _route_after_load_profiles,
        {"score_profile": "score_profile", "persist_and_publish": "persist_and_publish"},
    )
    graph.add_edge("score_profile", "select_best_profile")
    graph.add_edge("select_best_profile", "compute_recommendation")
    graph.add_edge("compute_recommendation", "persist_and_publish")
    graph.add_edge("persist_and_publish", END)

    return graph.compile()


__all__ = ["build_graph"]
