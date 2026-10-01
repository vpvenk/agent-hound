"""Scorers: (input, output, expected) -> a score from 0 to 1.

Every scorer here is plain Python that returns {"name", "score", "metadata"}. That dict is
Braintrust's scorer interface, and Langfuse takes the same three fields, which keeps the
graders portable if the platform is swapped (the doc's "keep graders as your own code").

Three kinds, cheapest first:
  citation_check          deterministic; mirrors the validate node
  root_cause_levenshtein  deterministic string distance (autoevals built-in); shows why wording-based scoring fails
  root_cause_judge        LLM judge, grades meaning; two engines share one rubric:
                            ClaudeJudge     our code, Anthropic SDK
                            autoevals_judge autoevals LLMClassifier, same rubric, routed through LiteLLM
  factuality              autoevals' built-in Factuality, off the shelf, for comparison
  RubricScorer            decision model (Julia-1 / Jev) answers the yes/no plan checks in checker.py
"""

from typing import Literal

import anthropic
from pydantic import BaseModel

from agenthound.evals.cost import record_usage

# Scorer metadata (rationales) per row, so the runner can save them in the local run file.
# Braintrust keeps these itself when logging is on; local mode drops them.
DETAILS: dict[tuple[str, str], dict] = {}


def _result(name: str, score: float, row_id: str, **metadata) -> dict:
    DETAILS[(row_id, name)] = metadata
    return {"name": name, "score": score, "metadata": metadata}


def _symptom(input: dict) -> str:
    i = input["incident"]
    return f"{i['signal']} on {i['table']}: {input['evidence'][0]['summary']}"


# --- 1. deterministic citation check ----------------------------------------

def citation_check(input, output, expected, metadata, **_):
    """Fraction of claims that cite at least one evidence id that exists in the case file.

    Same rule as the validate node, but graded instead of pass/fail. The node lets a plan
    through unvalidated after MAX_REVISIONS; this scorer is where that shows up.
    """
    claims = output["plan"]["claims"]
    known = set(output["evidence_ids"])
    bad = [c["text"] for c in claims if not c["cites"] or any(cid not in known for cid in c["cites"])]
    score = 0.0 if not claims else 1 - len(bad) / len(claims)
    return _result("citation_check", score, metadata["id"], failing_claims=bad, revisions=output["revisions"])


# --- 2. wording-based baseline ----------------------------------------------

def root_cause_levenshtein(input, output, expected, metadata, **_):
    from autoevals import Levenshtein

    s = Levenshtein()(output["plan"]["root_cause"], expected["root_cause"])
    return _result("root_cause_levenshtein", s.score, metadata["id"])


# --- 3. LLM judge: one rubric, two engines -----------------------------------

# Mustache placeholders, because that is autoevals' template format. ClaudeJudge fills
# them with str.replace. The rubric is ours either way.
ROOT_CAUSE_RUBRIC = """You are grading the root cause an automated investigator gave for a data incident.

[Incident]: {{input}}
[Reference root cause]: {{expected}}
[Candidate root cause]: {{output}}

Decide whether the candidate names the same underlying cause as the reference.
Judge meaning, not wording: "login token lapsed" and "credential expired" are the same cause.
Ignore length, tone, confidence and polish. A fluent, detailed answer that names a different cause is wrong.
A terse answer that names the right cause is right.

Choices:
match: the same underlying cause
partial: points at the right component or a symptom of the cause, but misses or blurs the actual cause
mismatch: a different cause, or no cause"""

CHOICE_SCORES = {"match": 1.0, "partial": 0.5, "mismatch": 0.0}


def render_rubric(input: str, output: str, expected: str) -> str:
    return (ROOT_CAUSE_RUBRIC.replace("{{input}}", input)
            .replace("{{output}}", output).replace("{{expected}}", expected))


class Verdict(BaseModel):
    rationale: str  # before choice, so the model reasons before it commits
    choice: Literal["match", "partial", "mismatch"]


class ClaudeJudge:
    """Our own judge on the Anthropic SDK. The engine to keep if Braintrust/autoevals go away."""

    def __init__(self, model: str = "claude-opus-5"):
        self.client = anthropic.Anthropic()
        self.model = model

    def grade(self, input: str, output: str, expected: str) -> Verdict:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": render_rubric(input, output, expected)
                       + "\n\nGive a rationale of one to three sentences, then the choice."}],
            output_format=Verdict,
        )
        record_usage("judge", self.model, response.usage)
        if response.parsed_output is None:
            raise RuntimeError(f"judge returned no verdict (stop_reason={response.stop_reason})")
        return response.parsed_output

    def __call__(self, input, output, expected, metadata, **_):
        v = self.grade(_symptom(input), output["plan"]["root_cause"], expected["root_cause"])
        return _result("root_cause_judge", CHOICE_SCORES[v.choice], metadata["id"],
                       choice=v.choice, rationale=v.rationale, judge_model=self.model)


def make_autoevals_scorers(model: str = "anthropic/claude-opus-5") -> list:
    """autoevals engines, routed to Claude through LiteLLM using ANTHROPIC_API_KEY.

    Two scorers so you can compare a custom rubric against an off-the-shelf one:
      autoevals_judge  LLMClassifier + OUR rubric
      factuality       built-in Factuality (subset/superset/disagree grading, its own prompt)
    """
    from autoevals import Factuality, LLMClassifier, init
    from autoevals.litellm import LiteLLMClient

    init(client=LiteLLMClient(), default_model=model)
    classifier = LLMClassifier(
        name="autoevals_judge",
        prompt_template=ROOT_CAUSE_RUBRIC,
        choice_scores=CHOICE_SCORES,
        use_cot=True,  # the rationale comes back in Score.metadata["rationale"]
    )
    factuality = Factuality()

    def autoevals_judge(input, output, expected, metadata, **_):
        s = classifier(output["plan"]["root_cause"], expected["root_cause"], input=_symptom(input))
        return _result("autoevals_judge", s.score, metadata["id"], **(s.metadata or {}))

    def factuality_scorer(input, output, expected, metadata, **_):
        s = factuality(output["plan"]["root_cause"], expected["root_cause"], input=_symptom(input))
        return _result("factuality", s.score, metadata["id"], **(s.metadata or {}))

    factuality_scorer.__name__ = "factuality"
    return [autoevals_judge, factuality_scorer]


# --- 4. rubric checks by a decision model -------------------------------------

class RubricScorer:
    """One score per PLAN_RUBRIC check (P(yes) from the decision model), plus the pass rate.

    Each check is its own named score so a run shows which checks fire, and a hand-labelled
    set can later measure the checker per question, not just overall.
    """

    def __init__(self, checker):
        self.checker = checker

    def __call__(self, input, output, expected, metadata, **_):
        from agenthound.evals.checker import render_state

        probs = self.checker.check(render_state(input, output["plan"]))
        passed = [qid for qid, p in probs.items() if p >= 0.5]
        _result("rubric", len(passed) / len(probs), metadata["id"], checker=self.checker.name,
                checker_model=self.checker.model, probabilities=probs,
                failed=[qid for qid in probs if qid not in passed])
        return [{"name": "rubric_pass_rate", "score": len(passed) / len(probs)}] + [
            {"name": f"rubric:{qid}", "score": p} for qid, p in probs.items()
        ]
