"""Swappable brains for the reason node. Each is a callable: case file in, state update out.

Passed via config={"configurable": {"reasoner": ...}} so the same graph runs any of them.
The eval harness compares them; that comparison is the point of layer 7.
"""

import re

import anthropic
from pydantic import BaseModel

from agenthound.evals.cost import record_usage
from agenthound.harness.state import IncidentState

# ---------------------------------------------------------------------------
# KeywordReasoner: offline, free, deterministic. A deliberately naive baseline.
# Scans evidence in order and takes the FIRST rule that matches, so a red
# herring early in the evidence list fools it (see inc-005). That is on purpose:
# a baseline that is sometimes wrong is what gives the scorers something to catch.
# ---------------------------------------------------------------------------

CAUSE_RULES = [
    (r"401|unauthori[sz]ed|credential", "Source authentication failed: the login credential lapsed",
     ["Rotate the credential", "Re-run the failed load"]),
    (r"does not exist|column .* dropped", "An upstream column was renamed or removed",
     ["Update the model to the new column name", "Re-run downstream models"]),
    (r"429|rate limit", "The source API throttled the extract so it stopped early",
     ["Re-sync the connector", "Reduce request rate"]),
    (r"resized|downsized", "The warehouse was made smaller, so the query runs out of memory and spills",
     ["Restore the warehouse size"]),
    (r"append|timed out after writing", "A retry loaded the same batch twice",
     ["Delete duplicate rows", "Make the load idempotent"]),
    (r"OutOfMemoryError", "The cluster is too small for this job",
     ["Increase cluster size"]),
    (r"first appears|not seen in prior", "The source introduced a new category value",
     ["Add the value to accepted values"]),
]


class KeywordReasoner:
    def __init__(self, cite: bool = True):
        self.cite = cite  # cite=False is the "break it" switch: claims lose their evidence

    def __call__(self, state: IncidentState) -> dict:
        evidence = state["evidence"]
        symptom = evidence[0]
        claims = [{"text": f"Observed: {symptom['summary']}", "cites": [symptom["id"]]}]
        for pattern, cause, steps in CAUSE_RULES:
            hit = next((e for e in evidence if re.search(pattern, e["summary"], re.I)), None)
            if hit:
                claims.append({"text": cause, "cites": [hit["id"]] if self.cite else []})
                return {"tool_request": None, "claims": claims, "root_cause": cause, "next_steps": steps}
        return {"tool_request": None, "claims": claims, "root_cause": "Undetermined", "next_steps": ["Escalate to on-call"]}


# ---------------------------------------------------------------------------
# ClaudeReasoner: the real thing. Structured output so the claims and cites come
# back as data the validator can check, not prose we would have to parse.
# ---------------------------------------------------------------------------

PROMPTS = {
    "v1": """You are the reasoning step of a data-incident investigation.
You receive one incident and the evidence gathered for it. Work out the single underlying root cause.

Rules:
- Distinguish the cause from its symptoms. A failed job, an OOM, or a stale table is usually a symptom.
- Evidence can contain red herrings. Prefer the explanation the evidence actually supports, and notice evidence that rules things out.
- Every claim must cite at least one evidence id from the list you are given. Never cite an id that is not in the list.
- If the evidence does not support a root cause, say so in root_cause rather than guessing.
- root_cause is one sentence. next_steps are concrete actions an on-call data engineer can take.""",
    # Deliberately worse prompt for the "break it and watch the scores move" drill.
    "v0-broken": """Glance at the incident and give your best quick guess at what went wrong.
Citations are optional.""",
}


class ClaimOut(BaseModel):
    text: str
    cites: list[str]


class Diagnosis(BaseModel):
    root_cause: str
    claims: list[ClaimOut]
    next_steps: list[str]


class ClaudeReasoner:
    def __init__(self, model: str = "claude-opus-5", prompt: str = "v1"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.system = PROMPTS[prompt]

    def __call__(self, state: IncidentState) -> dict:
        incident = {k: v for k, v in state["incident"].items() if k != "evidence"}
        evidence = "\n".join(f"[{e['id']}] ({e['source']}) {e['summary']}" for e in state["evidence"])
        content = f"Incident: {incident}\n\nEvidence:\n{evidence}"
        if state["validation_errors"]:
            # The validate -> reason loop: tell the model exactly what the validator rejected.
            content += "\n\nYour previous answer was rejected by the validator:\n- " + "\n- ".join(state["validation_errors"])

        # No server-side refusal fallback here on purpose: in an eval, a silent switch to
        # another model would credit that model's answer to this one. A refusal should show
        # up as a failed row instead.
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=self.system,
            messages=[{"role": "user", "content": content}],
            output_format=Diagnosis,
        )
        record_usage("reasoner", self.model, response.usage)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"reasoner returned no diagnosis (stop_reason={response.stop_reason})")
        d = response.parsed_output
        return {
            "tool_request": None,
            "claims": [c.model_dump() for c in d.claims],
            "root_cause": d.root_cause,
            "next_steps": d.next_steps,
        }
