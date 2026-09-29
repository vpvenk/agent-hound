from agenthound.harness.graph import build_graph
from agenthound.harness.state import new_case_file

_graph = build_graph()


def run_harness(input: dict, reasoner) -> dict:
    """The task under test: incident + evidence in, plan out. It never sees `expected`."""
    incident = {**input["incident"], "evidence": input["evidence"]}
    final = _graph.invoke(new_case_file(incident), config={"configurable": {"reasoner": reasoner}})
    return {
        "plan": final["resolution_plan"],
        # What the case file held, so the citation scorer can check cites against it.
        "evidence_ids": [e["id"] for e in final["evidence"]],
        "revisions": final["revision_count"],
    }
