from langgraph.graph import END, START, StateGraph

from agenthound.harness.nodes import gather_context, reason, tools, triage, validate, write_plan
from agenthound.harness.state import IncidentState

MAX_REVISIONS = 3


def route_after_reason(state: IncidentState) -> str:
    return "tools" if state["tool_request"] else "validate"


def route_after_validate(state: IncidentState) -> str:
    if not state["validation_errors"]:
        return "write_plan"
    # Limit check comes before "errors -> reason", otherwise a reason that never cites loops forever
    if state["revision_count"] >= MAX_REVISIONS:
        return "write_plan"
    return "reason"


def build_graph():
    graph = StateGraph(IncidentState)

    graph.add_node("triage", triage)
    graph.add_node("gather_context", gather_context)
    graph.add_node("reason", reason)
    graph.add_node("tools", tools)
    graph.add_node("validate", validate)
    graph.add_node("write_plan", write_plan)

    graph.add_edge(START, "triage")
    graph.add_edge("triage", "gather_context")
    graph.add_edge("gather_context", "reason")
    graph.add_conditional_edges("reason", route_after_reason, ["tools", "validate"])
    graph.add_edge("tools", "reason")
    graph.add_conditional_edges("validate", route_after_validate, ["reason", "write_plan"])
    graph.add_edge("write_plan", END)

    return graph.compile()
