"""Row-by-row diff of two saved runs: the offline stand-in for Braintrust's experiment diff.

  uv run python scripts/diff_runs.py evals/runs/A.json evals/runs/B.json
  uv run python scripts/diff_runs.py            # the two most recent runs
"""

import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "evals" / "runs"


def main():
    paths = [Path(p) for p in sys.argv[1:3]] or sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime)[-2:]
    if len(paths) != 2:
        sys.exit("need two runs")
    a, b = (json.loads(p.read_text()) for p in paths)
    print(f"A = {a['name']}\nB = {b['name']}\n")

    scorer_names = sorted({s for r in a["rows"] + b["rows"] for s in (r["scores"] or {})})
    rows_b = {r["id"]: r for r in b["rows"]}
    better = worse = 0
    for ra in a["rows"]:
        rb = rows_b.get(ra["id"])
        if rb is None:
            continue
        changes = []
        for s in scorer_names:
            va, vb = (ra["scores"] or {}).get(s), (rb["scores"] or {}).get(s)
            if va is None or vb is None or abs(va - vb) < 1e-9:
                continue
            changes.append(f"{s} {va:.2f} -> {vb:.2f}")
            better += vb > va
            worse += vb < va
        if changes:
            print(f"{ra['id']}  " + "; ".join(changes))
            if ra["root_cause"] != rb["root_cause"]:
                print(f"    A: {ra['root_cause']}\n    B: {rb['root_cause']}")
    print(f"\n{better} score(s) improved, {worse} regressed")
    for m in sorted(set(a["metrics"]) | set(b["metrics"])):
        print(f"  {m:<24} {a['metrics'].get(m, '-')!s:>8} -> {b['metrics'].get(m, '-')!s:>8}")


if __name__ == "__main__":
    main()
