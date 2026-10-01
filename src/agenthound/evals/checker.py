"""Rubric checker: a decision model answers yes/no questions about a plan, cheaply and fast.

The split this encodes: a decision model (Julia-1 local, Jev hosted) reads text and compares
it to the options it is given, so it only gets questions whose answer is VISIBLE in the plan
("does it name the affected table?", "is there a verification step?"). Whether the plan is
actually right needs reasoning and outside facts; that stays with the LLM judge.

Both engines take the same question dict ({type: noul, instructions, criteria: {false, true}})
and return P(yes) per question, all questions in one call.

  JuliaChecker  SupersonicLabs/Julia-1, 144M params, runs on CPU, nothing leaves the machine
  JevChecker    TypeSafe Jev, hosted API, needs TYPESAFE_API_KEY; every call sends the plan out
"""

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from agenthound.evals.cost import record_usage

# Keep every description under 48 tokens: Julia-1 rejects longer options under strict encoding.
PLAN_RUBRIC: dict[str, dict] = {
    "names_affected_asset": {
        "instructions": "Does the plan name the affected table or job from the incident?",
        "criteria": {"false": "The plan never names the affected table or job",
                     "true": "The plan names the affected table or job"},
    },
    "states_specific_cause": {
        "instructions": "Does the plan state a specific root cause?",
        "criteria": {"false": "No cause, 'undetermined', or it only restates the symptom",
                     "true": "It names a specific underlying cause"},
    },
    "single_course_of_action": {
        "instructions": "Does the plan commit to one course of action?",
        "criteria": {"false": "It offers several alternative options to choose between",
                     "true": "It gives one recommended sequence of steps"},
    },
    "concrete_steps": {
        "instructions": "Are the next steps concrete actions an engineer can carry out?",
        "criteria": {"false": "Steps are vague, such as investigate, monitor, or escalate",
                     "true": "Steps are specific actions on named systems"},
    },
    "includes_verification": {
        "instructions": "Does the plan include a step that confirms the fix worked?",
        "criteria": {"false": "No re-run, rebuild, or check after the fix",
                     "true": "It re-runs, rebuilds, or checks the result after the fix"},
    },
    "steps_address_cause": {
        "instructions": "Do the next steps act on the stated root cause?",
        "criteria": {"false": "The steps only treat the symptom or an unrelated system",
                     "true": "The steps act directly on the stated root cause"},
    },
    "no_unguarded_destructive_action": {
        "instructions": "Does the plan avoid destructive actions without a safeguard?",
        "criteria": {"false": "It deletes, drops, or overwrites data with no backup or check",
                     "true": "No destructive action, or any such action has a safeguard"},
    },
}


def questions() -> dict[str, dict]:
    return {qid: {"type": "noul", **q} for qid, q in PLAN_RUBRIC.items()}


def render_state(input: dict, plan: dict) -> str:
    """What the checker reads: the incident, its evidence, and the plan. Never the reference."""
    incident = {k: v for k, v in input["incident"].items() if k != "evidence"}
    evidence = "\n".join(f"[{e['id']}] ({e['source']}) {e['summary']}" for e in input["evidence"])
    claims = "\n".join(f"- {c['text']} (cites: {', '.join(c['cites']) or 'none'})" for c in plan["claims"])
    steps = "\n".join(f"{n}. {s}" for n, s in enumerate(plan["next_steps"], start=1))
    return (f"Incident: {json.dumps(incident)}\n\nEvidence:\n{evidence}\n\n"
            f"Resolution plan\nRoot cause: {plan['root_cause']}\nClaims:\n{claims}\nNext steps:\n{steps}")


DEFAULT_JULIA_CHECKPOINT = Path(__file__).resolve().parents[4] / "julia-1-benchmarks" / "Julia-1"


class JuliaChecker:
    name = "julia"

    def __init__(self, checkpoint: str | Path | None = None, device: str = "cpu"):
        checkpoint = Path(checkpoint or os.environ.get("JULIA_CHECKPOINT") or DEFAULT_JULIA_CHECKPOINT)
        if not (checkpoint / "model.safetensors").exists():
            raise FileNotFoundError(
                f"no Julia-1 weights at {checkpoint}; download SupersonicLabs/Julia-1 and pass "
                "--julia-checkpoint or set JULIA_CHECKPOINT")
        # The julia package ships inside the checkpoint folder rather than on PyPI.
        sys.path.insert(0, str(checkpoint))
        from julia import load_model

        self.model = "SupersonicLabs/Julia-1"
        self.engine = load_model(str(checkpoint), device=device, strict_encoding=True,
                                 max_length=1024, head_length=512)
        self._lock = threading.Lock()  # Eval runs rows on 4 threads; one resident engine

    def check(self, state: str) -> dict[str, float]:
        with self._lock:
            result = self.engine.predict(state=state, questions=questions())
        return {qid: float(a["noul"]) for qid, a in result["answers"].items()}


class JevChecker:
    name = "jev"

    def __init__(self, model: str = "jev-latest"):
        from typesafe_sdk import TypeSafeClient

        self.model = model
        self.client = TypeSafeClient(model=model)
        self.resolved: set[str] = set()

    def check(self, state: str) -> dict[str, float]:
        response = self.client.system_one(state=state, questions=questions(), model=self.model)
        self.resolved.add(response.model)
        usage = response.usage
        record_usage("checker", "jev", SimpleNamespace(input_tokens=usage.input_tokens or 0,
                                                       output_tokens=usage.output_tokens or 0))
        return {qid: float(a.noul) for qid, a in response.nouls.items()}
