from langchain_core.runnables import RunnableConfig

from agenthound.harness.state import IncidentState


def triage(state: IncidentState) -> dict:
    # Step 1: a hard-coded rule. Later this is the composer's job (Layer 3).
    signal = state["incident"]["signal"]
    category = "volume_drop" if signal == "row_count_anomaly" else "unknown"
    return {"category": category}


def gather_context(state: IncidentState) -> dict:
    # Step 1: fake evidence. Later this reads the lineage graph + case files (Layer 2).
    # Eval fixtures carry their own evidence, standing in for that read.
    if "evidence" in state["incident"]:
        return {"evidence": state["incident"]["evidence"]}
    table = state["incident"]["table"]
    return {
        "evidence": [
            {"id": "ev-1", "source": "lineage", "summary": f"{table} is built from raw.orders"},
            {"id": "ev-2", "source": "job_history", "summary": "orders_load job failed at 02:14 UTC"},
            {"id": "ev-3", "source": "case_files", "summary": "Same failure 3 weeks ago: expired source credentials"},
        ]
    }


def reason(state: IncidentState, config: RunnableConfig) -> dict:
    # A reasoner passed in config (see harness/reasoners.py) replaces the stub below.
    # Config, not state: which brain runs is a property of the run, not of the case file.
    reasoner = config.get("configurable", {}).get("reasoner")
    if reasoner is not None:
        return reasoner(state)

    # STUB standing in for the LLM. It walks a fixed script so one run exercises both loops:
    #   pass 1 -> asks for a tool             (reason -> tools -> reason)
    #   pass 2 -> one claim forgets to cite   (validate -> reason)
    #   pass 3 -> every claim cited           (validate -> write_plan)
    has_tool_result = any(e["source"] == "row_count_comparator" for e in state["evidence"])

    if not has_tool_result:
        return {
            "tool_request": {"name": "row_count_comparator", "args": {"table": state["incident"]["table"]}},
            "claims": [],
        }

    if state["revision_count"] == 0:
        return {
            "tool_request": None,
            "claims": [
                {"text": "Row count dropped because orders_load failed", "cites": ["ev-2", "ev-4"]},
                {"text": "Root cause is expired source credentials", "cites": []},  # deliberately uncited
            ],
        }

    return {
        "tool_request": None,
        "claims": [
            {"text": "Row count dropped because orders_load failed", "cites": ["ev-2", "ev-4"]},
            {"text": "Root cause is expired source credentials", "cites": ["ev-3"]},
        ],
        "root_cause": "Expired source credentials broke orders_load",
        "next_steps": [
            "Rotate the orders source credentials",
            "Re-run orders_load, then rebuild daily_revenue",
        ],
    }


def tools(state: IncidentState) -> dict:
    # STUB: no real tools until build step 2. Whatever runs, its output becomes citable evidence.
    request = state["tool_request"]
    result = {
        "id": "ev-4",
        "source": request["name"],
        "summary": "daily_revenue: 1,204 rows today vs 21,880 yesterday (-94%)",
    }
    return {"evidence": [result], "tool_request": None}  # reset, or the router loops back to tools forever


def validate(state: IncidentState) -> dict:
    evidence_ids = {e["id"] for e in state["evidence"]}
    errors: list[str] = []

    if not state["claims"]:
        errors.append("No claims provided")

    for idx, claim in enumerate(state["claims"]):
        # Rule 1: must be checked outside the inner loop — an empty list never enters it
        if not claim["cites"]:
            errors.append(f"Claim {idx} cites no evidence")

        # Rule 2: report every unknown id so reason can fix them all in one revision
        for cited_id in claim["cites"]:
            if cited_id not in evidence_ids:
                errors.append(f"Claim {idx} cites unknown evidence id '{cited_id}'")

    return {
        "validation_errors": errors,
        "revision_count": state["revision_count"] + (1 if errors else 0),
    }


def write_plan(state: IncidentState) -> dict:
    # The ONE plan object every surface will render. Printed to console for now.
    return {
        "resolution_plan": {
            "incident": state["incident"]["table"],
            "category": state["category"],
            "validated": not state["validation_errors"],  # False = gave up after MAX_REVISIONS
            "root_cause": state["root_cause"],
            "claims": state["claims"],
            "next_steps": state["next_steps"],
        }
    }
