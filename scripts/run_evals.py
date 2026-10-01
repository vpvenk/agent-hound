"""Run one experiment: the harness over every fixture, graded by the scorers.

  uv run python scripts/run_evals.py                          # offline: keyword reasoner, no judge
  uv run python scripts/run_evals.py --reasoner keyword-nocite # break it, watch citation_check drop
  uv run python scripts/run_evals.py --reasoner claude --judge claude
  uv run --extra julia python scripts/run_evals.py --checker julia   # + yes/no plan rubric, local
  uv run python scripts/run_evals.py --gate                    # CI: exit 1 if worse than evals/baseline.json
  uv run python scripts/run_evals.py --set-baseline            # accept this run as the new bar

With BRAINTRUST_API_KEY set, the run is logged as a Braintrust experiment (row-by-row diff
in the UI). Without it, Braintrust runs locally and this script writes evals/runs/<name>.json;
scripts/diff_runs.py gives the same row-by-row diff offline.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from braintrust import Eval

from agenthound.evals import cost, scorers
from agenthound.evals.dataset import load_fixtures, to_eval_rows
from agenthound.evals.task import run_harness
from agenthound.harness.reasoners import ClaudeReasoner, KeywordReasoner

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "evals" / "runs"
DEFAULT_BASELINE = ROOT / "evals" / "baseline.json"


def build_reasoner(args):
    if args.reasoner == "keyword":
        return KeywordReasoner()
    if args.reasoner == "keyword-nocite":
        return KeywordReasoner(cite=False)
    return ClaudeReasoner(model=args.model, prompt=args.prompt)


def build_checker(args):
    if args.checker == "julia":
        from agenthound.evals.checker import JuliaChecker

        return JuliaChecker(args.julia_checkpoint)
    if args.checker == "jev":
        from agenthound.evals.checker import JevChecker

        return JevChecker(args.jev_model)
    return None


def build_scorers(args) -> list:
    chosen = [scorers.citation_check, scorers.root_cause_levenshtein]
    checker = build_checker(args)
    if checker is not None:
        chosen.append(scorers.RubricScorer(checker))
    if args.judge == "claude":
        chosen.append(scorers.ClaudeJudge(model=args.judge_model))
    elif args.judge == "autoevals":
        chosen += scorers.make_autoevals_scorers(model=f"anthropic/{args.judge_model}")
    elif args.judge == "both":
        chosen.append(scorers.ClaudeJudge(model=args.judge_model))
        chosen += scorers.make_autoevals_scorers(model=f"anthropic/{args.judge_model}")
    return chosen


def summarize(results) -> dict:
    by_scorer: dict[str, list[float]] = {}
    for r in results:
        for name, value in (r.scores or {}).items():
            if value is not None:
                by_scorer.setdefault(name, []).append(value)
    metrics = {name: round(sum(v) / len(v), 4) for name, v in by_scorer.items()}
    cites = by_scorer.get("citation_check", [])
    if cites:
        metrics["citation_pass_rate"] = round(sum(1 for v in cites if v == 1.0) / len(cites), 4)
    metrics["errored_rows"] = sum(1 for r in results if r.error)
    return metrics


def gate(metrics: dict, baseline: Path, tolerance: float) -> int:
    if not baseline.exists():
        print(f"GATE: no baseline at {baseline}. Run with --set-baseline first.")
        return 1
    base = json.loads(baseline.read_text())["metrics"]
    failures = []
    for name, base_value in base.items():
        if name == "errored_rows":
            if metrics.get(name, 0) > base_value:
                failures.append(f"{name}: {metrics[name]} > baseline {base_value}")
            continue
        if name not in metrics:
            failures.append(f"{name}: not measured this run (baseline has it; same judge settings?)")
        elif metrics[name] < base_value - tolerance:
            failures.append(f"{name}: {metrics[name]:.3f} < baseline {base_value:.3f}")
    if failures:
        print("GATE: FAIL\n  " + "\n  ".join(failures))
        return 1
    print("GATE: pass (no metric below baseline)")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reasoner", choices=["keyword", "keyword-nocite", "claude"], default="keyword")
    p.add_argument("--model", default="claude-opus-5", help="reasoner model")
    p.add_argument("--prompt", choices=["v1", "v0-broken"], default="v1", help="reasoner prompt version")
    p.add_argument("--judge", choices=["none", "claude", "autoevals", "both"], default="none")
    p.add_argument("--judge-model", default="claude-opus-5")
    p.add_argument("--checker", choices=["none", "julia", "jev"], default="none",
                   help="decision model for the yes/no plan rubric (needs --extra julia / --extra jev)")
    p.add_argument("--julia-checkpoint", type=Path,
                   help="Julia-1 download (default: $JULIA_CHECKPOINT or ../julia-1-benchmarks/Julia-1)")
    p.add_argument("--jev-model", default="jev-latest")
    p.add_argument("--limit", type=int, help="first N fixtures only (cheap smoke runs)")
    p.add_argument("--name", help="experiment name (default: derived from the settings)")
    p.add_argument("--gate", action="store_true")
    p.add_argument("--tolerance", type=float, default=0.0)
    p.add_argument("--set-baseline", action="store_true")
    p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                   help="one baseline per reasoner/judge setup; metrics are only comparable within one")
    args = p.parse_args()

    rows = to_eval_rows(load_fixtures())[: args.limit]
    reasoner = build_reasoner(args)
    label = args.reasoner if args.reasoner != "claude" else f"claude-{args.prompt}"
    check = f"__check-{args.checker}" if args.checker != "none" else ""
    name = args.name or f"{label}__judge-{args.judge}{check}__{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"

    result = Eval(
        "agent-hound",
        experiment_name=name,
        data=lambda: rows,
        task=lambda input: run_harness(input, reasoner),
        scores=build_scorers(args),
        metadata={"reasoner": label, "judge": args.judge, "judge_model": args.judge_model,
                  "checker": args.checker},
        max_concurrency=4,
        no_send_logs=not os.environ.get("BRAINTRUST_API_KEY"),
    )

    metrics = summarize(result.results)
    run = {
        "name": name,
        "settings": {k: str(v) for k, v in vars(args).items()},
        "metrics": metrics,
        "rows": [
            {
                "id": r.metadata["id"],
                "difficulty": r.metadata["difficulty"],
                "root_cause": r.output["plan"]["root_cause"] if r.output else None,
                "expected_root_cause": r.expected["root_cause"],
                "scores": r.scores,
                "details": {k[1]: v for k, v in scorers.DETAILS.items() if k[0] == r.metadata["id"]},
                "error": str(r.error) if r.error else None,
            }
            for r in result.results
        ],
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{name}.json"
    out.write_text(json.dumps(run, indent=2, default=str))

    print("\nmetrics:", json.dumps(metrics, indent=2))
    print(cost.report(len(rows)))
    print(f"run saved: {out.relative_to(ROOT)}")

    if args.set_baseline:
        args.baseline.write_text(json.dumps({"from_run": name, "settings": {k: v for k, v in run["settings"].items() if k != "baseline"}, "metrics": metrics}, indent=2))
        print(f"baseline set: {args.baseline}")
    if args.gate:
        sys.exit(gate(metrics, args.baseline, args.tolerance))


if __name__ == "__main__":
    main()
