"""Assemble the review graph.

    parse_diff -> compute_impact -> classify_risk -> (needs approval?) -> human_approval -> report
                                                  \\-> (no) ------------------------> report

The judge is handed in when the graph is built, not stored in the state: it is a live object
(a model connection) and has no place in a checkpoint. Tests pass a FakeJudge.
"""

from langgraph.graph import END, START, StateGraph

from schema_impact.agent.checkpoint import make_checkpointer
from schema_impact.agent.judge import Judge
from schema_impact.agent.nodes import (
    compute_impact_node,
    human_approval_node,
    make_classify_risk_node,
    parse_diff_node,
    report_node,
)
from schema_impact.agent.state import ReviewState


def _next_after_classify(state: ReviewState) -> str:
    return "human_approval" if state["needs_approval"] else "report"


def build_graph(judge: Judge, checkpointer=None):
    """Build the review graph.

    A review that needs approval pauses, and a paused graph can only be resumed if its state
    was saved. So when no checkpointer is given, an in-memory one is used. Pass your own to
    share saved reviews between graph objects.
    """
    graph = StateGraph(ReviewState)
    graph.add_node("parse_diff", parse_diff_node)
    graph.add_node("compute_impact", compute_impact_node)
    graph.add_node("classify_risk", make_classify_risk_node(judge))
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("report", report_node)

    graph.add_edge(START, "parse_diff")
    graph.add_edge("parse_diff", "compute_impact")
    graph.add_edge("compute_impact", "classify_risk")
    graph.add_conditional_edges(
        "classify_risk",
        _next_after_classify,
        {"human_approval": "human_approval", "report": "report"},
    )
    graph.add_edge("human_approval", "report")
    graph.add_edge("report", END)

    return graph.compile(checkpointer=checkpointer or make_checkpointer())
