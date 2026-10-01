# agent-hound
POC Skeleton of E2E Agentic Incident Investigation

## Evals (layer 7)

The harness is the task; fixtures are the dataset; scorers grade each plan; `run_evals.py`
is one experiment; `--gate` is the CI gate.

| Piece | Where |
|---|---|
| Dataset: 10 hand-written incidents with reference answers, traps, and judge probes | `evals/fixtures/incidents.json` |
| Task: incident + evidence in, plan out (never sees the reference) | `src/agenthound/evals/task.py` |
| Reasoners: offline `KeywordReasoner`, real `ClaudeReasoner` (prompt `v1` / `v0-broken`) | `src/agenthound/harness/reasoners.py` |
| Scorers: citation check, Levenshtein, Claude judge, autoevals judge + Factuality | `src/agenthound/evals/scorers.py` |
| Rubric checker: Julia-1 (local) or Jev (hosted) answers yes/no plan checks | `src/agenthound/evals/checker.py` |
| Experiment runner + gate | `scripts/run_evals.py` |
| Row-by-row diff of two runs (offline Braintrust diff) | `scripts/diff_runs.py` |
| Judge calibration / self-preference probe | `scripts/probe_judge.py` |

```bash
# offline, free
uv run python scripts/run_evals.py                              # keyword reasoner
uv run python scripts/run_evals.py --reasoner keyword-nocite --gate   # break it; gate exits 1
uv run python scripts/diff_runs.py                              # what changed, row by row

# needs ANTHROPIC_API_KEY (BRAINTRUST_API_KEY optional: logs to the Braintrust UI)
uv run python scripts/probe_judge.py                            # trust the judge first (20 calls)
uv run python scripts/run_evals.py --reasoner claude --judge claude --limit 2   # smoke, check cost
uv run python scripts/run_evals.py --reasoner claude --judge both
uv run python scripts/run_evals.py --reasoner claude --prompt v0-broken --judge claude
```

### Rubric checker (Julia-1 / Jev)

A decision model answers the yes/no checks in `PLAN_RUBRIC` (names the affected table? one course
of action? a verification step?) and each check becomes its own score (`rubric:<check>`, plus
`rubric_pass_rate`). Only checks visible in the plan's text go here; whether the plan is right
stays with the LLM judge. Julia-1 vs Jev head to head on classification: `../julia-1-benchmarks/`.

```bash
# Julia-1: local CPU, free, nothing leaves the machine. The julia package ships inside the
# checkpoint folder, so download the full repo (577 MB):
uvx --from huggingface_hub hf download SupersonicLabs/Julia-1 --local-dir ../julia-1-benchmarks/Julia-1
uv run --extra julia python scripts/run_evals.py --checker julia       # or --julia-checkpoint PATH

# Jev: hosted, sends each plan to TypeSafe; one call per row covers every check
export TYPESAFE_API_KEY=...
uv run --extra jev python scripts/run_evals.py --checker jev
```

Not trustworthy yet: on the keyword plans Julia-1 swings from 0.0 to 0.97 on the same check
depending on the wording and how much of the incident it is shown. Hand-label the checks on
~50 plans and measure each question before letting `rubric:*` feed the judge or a gate.
