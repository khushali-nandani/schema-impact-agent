"""Assemble the review graph.

    parse_diff -> compute_impact -> classify_risk -> report

The judge is handed in when the graph is built, not stored in the state: it is a live object
(a model connection) and has no place in a checkpoint. Tests pass a FakeJudge.
"""

from langgraph.graph import END, START, StateGraph

from schema_impact.agent.judge import Judge
from schema_impact.agent.nodes import (
    compute_impact_node,
    make_classify_risk_node,
    parse_diff_node,
    report_node,
)
from schema_impact.agent.state import ReviewState


def build_graph(judge: Judge, checkpointer=None):
    graph = StateGraph(ReviewState)
    graph.add_node("parse_diff", parse_diff_node)
    graph.add_node("compute_impact", compute_impact_node)
    graph.add_node("classify_risk", make_classify_risk_node(judge))
    graph.add_node("report", report_node)

    graph.add_edge(START, "parse_diff")
    graph.add_edge("parse_diff", "compute_impact")
    graph.add_edge("compute_impact", "classify_risk")
    graph.add_edge("classify_risk", "report")
    graph.add_edge("report", END)

    return graph.compile(checkpointer=checkpointer)
