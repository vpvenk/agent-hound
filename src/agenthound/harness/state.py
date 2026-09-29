import operator 
from typing import Annotated, Optional, TypedDict 

class Evidence(TypedDict): 
    id: str  # e.g "evidence-1" - what claims cite 
    source: str # "lineage", "row_count_comparator"
    summary: str 


class Claim(TypedDict): 
    text: str 
    cites: list[str] # evidence ids backing this claim; empty = uncited -> validate fails it 


class ToolRequest(TypedDict): 
    name: str 
    args: dict 


class IncidentState(TypedDict):
    incident: dict 
    category: Optional[str] 
    evidence: Annotated[list[Evidence], operator.add] # The only appending field 
    claims: list[Claim] 
    tool_request: Optional[ToolRequest] # None = no tool wanted 
    validation_errors: list[str] 
    revision_count: int # how many times validate sent claims back to reason
    root_cause: Optional[str] # one sentence; what the eval judge grades
    next_steps: list[str]
    resolution_plan: Optional[dict] 


def new_case_file(incident: dict) -> IncidentState:
    return {
        "incident": incident,
        "category": None,
        "evidence": [],
        "claims": [],
        "tool_request": None,
        "validation_errors": [],
        "revision_count": 0,
        "root_cause": None,
        "next_steps": [],
        "resolution_plan": None,
    }

