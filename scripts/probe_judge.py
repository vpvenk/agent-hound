"""Calibrate the judge before trusting it: the self-preference / style-bias check.

Each fixture carries two hand-written probes the task never sees:
  human_correct  terse, informal, RIGHT cause  ("j.doe paused the DAG")
  fluent_wrong   polished, confident, WRONG cause, in the style a model writes
A judge that grades meaning scores human_correct ~1 and fluent_wrong ~0. A judge that
rewards its own writing style drifts the other way, and this shows by how much.

Cross-judge check: run it with two judge models and compare, e.g.
  uv run python scripts/probe_judge.py --judge-model claude-opus-5
  uv run python scripts/probe_judge.py --judge-model claude-sonnet-5
A real self-preference guard also needs a judge from another model family than the
reasoner (autoevals + LiteLLM make that a model-string change once a key exists).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

from agenthound.evals import cost
from agenthound.evals.dataset import load_fixtures
from agenthound.evals.scorers import CHOICE_SCORES, ClaudeJudge


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--judge-model", default="claude-opus-5")
    args = p.parse_args()

    judge = ClaudeJudge(model=args.judge_model)
    fixtures = load_fixtures()

    def grade(f, kind):
        i = f["input"]
        symptom = f"{i['incident']['signal']} on {i['incident']['table']}: {i['evidence'][0]['summary']}"
        v = judge.grade(symptom, f["probes"][kind], f["expected"]["root_cause"])
        return f["id"], kind, v

    jobs = [(f, k) for f in fixtures for k in ("human_correct", "fluent_wrong")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: grade(*j), jobs))

    scores = {"human_correct": [], "fluent_wrong": []}
    for fid, kind, v in results:
        s = CHOICE_SCORES[v.choice]
        scores[kind].append(s)
        wrong = (kind == "human_correct" and s < 1) or (kind == "fluent_wrong" and s > 0)
        if wrong:
            print(f"MISJUDGED {fid} {kind}: {v.choice} - {v.rationale}")

    hc = sum(scores["human_correct"]) / len(scores["human_correct"])
    fw = sum(scores["fluent_wrong"]) / len(scores["fluent_wrong"])
    print(f"\njudge {args.judge_model}")
    print(f"  human_correct mean {hc:.2f}   (want 1.00: credits right-but-terse answers)")
    print(f"  fluent_wrong  mean {fw:.2f}   (want 0.00: not fooled by polish)")
    print(f"  separation    {hc - fw:+.2f}   (want +1.00; near 0 = the judge can't tell right from polished)")
    print(cost.report(len(jobs)))


if __name__ == "__main__":
    main()
