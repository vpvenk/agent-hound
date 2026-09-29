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
