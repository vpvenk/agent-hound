# agent-hound
POC Skeleton of E2E Agentic Incident Investigation

A LangGraph harness takes a data incident, gathers evidence, reasons to a root cause, validates
that every claim cites evidence, and writes one resolution plan. The evals track grades those
plans.

```
triage -> gather_context -> reason <-> tools
                              |  ^
                              v  |  (uncited claims go back, up to MAX_REVISIONS)
                            validate -> write_plan
```

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # core: harness, scorers, Braintrust runner
uv sync --extra julia            # + Julia-1 rubric checker (torch, transformers)
uv sync --extra jev              # + Jev rubric checker (TypeSafe SDK)
```

API keys, only for the paths that need them:

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | `--reasoner claude`, `--judge claude/autoevals/both`, `probe_judge.py` |
| `BRAINTRUST_API_KEY` | optional: logs each run as a Braintrust experiment instead of local-only |
| `TYPESAFE_API_KEY` | `--checker jev` |
| `JULIA_CHECKPOINT` | optional: Julia-1 location if not `../julia-1-benchmarks/Julia-1` |

Keep keys in your shell (`export ...`) or an untracked `.env`/`.envrc` (both are gitignored).

## Harness demo

```bash
uv run python scripts/run_fake_incident.py              # one fake incident, node by node
uv run python scripts/run_fake_incident.py --delay 0    # same, no pause between nodes
uv run python scripts/draw_graph.py                     # writes graph.html, opens it, highlights the path taken
```

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
| Token and dollar tally per component | `src/agenthound/evals/cost.py` |
| Experiment runner + gate | `scripts/run_evals.py` |
| Row-by-row diff of two runs (offline Braintrust diff) | `scripts/diff_runs.py` |
| Judge calibration / self-preference probe | `scripts/probe_judge.py` |

### Running

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

Start model-backed runs with `--limit 2`: every run prints its cost per component and per row.

### `run_evals.py` flags

| Flag | Default | What it does |
|---|---|---|
| `--reasoner keyword\|keyword-nocite\|claude` | `keyword` | Who writes the plan. `keyword-nocite` drops citations on purpose |
| `--model` | `claude-opus-5` | Reasoner model for `--reasoner claude` |
| `--prompt v1\|v0-broken` | `v1` | Reasoner prompt version; `v0-broken` is the deliberately bad one |
| `--judge none\|claude\|autoevals\|both` | `none` | LLM judge for the root cause |
| `--judge-model` | `claude-opus-5` | Judge model |
| `--checker none\|julia\|jev` | `none` | Decision model for the yes/no plan rubric (see below) |
| `--julia-checkpoint PATH` | `$JULIA_CHECKPOINT` or `../julia-1-benchmarks/Julia-1` | Julia-1 download |
| `--jev-model` | `jev-latest` | Jev model to request |
| `--limit N` | all | First N fixtures only |
| `--name` | from settings | Experiment name, e.g. `keyword__judge-none__check-julia__<timestamp>` |
| `--gate` | off | Exit 1 if any metric is below the baseline |
| `--tolerance` | `0.0` | How far below the baseline a metric may drop before the gate fails |
| `--set-baseline` | off | Accept this run's metrics as the new baseline |
| `--baseline PATH` | `evals/baseline.json` | Baseline file; keep one per reasoner/judge setup |

### Outputs

- **Summary**: mean per scorer, `citation_pass_rate`, `errored_rows`, and the cost report, printed
  at the end of each run.
- **Run file**: `evals/runs/<name>.json` with settings, metrics, and per row the plan's root
  cause, the reference, every score, and scorer details (judge rationales, failing claims,
  rubric probabilities). Run files are gitignored; `evals/baseline.json` is committed.
- **Diff**: `scripts/diff_runs.py` compares the two most recent runs, or two you name:
  `uv run python scripts/diff_runs.py evals/runs/A.json evals/runs/B.json`.

### Gate and baselines

Metrics are only comparable within one reasoner/judge/checker setup, so each setup gets its own
baseline file.

```bash
uv run python scripts/run_evals.py --set-baseline               # offline baseline (evals/baseline.json)
uv run python scripts/run_evals.py --reasoner claude --judge claude \
  --baseline evals/baseline_claude.json --set-baseline          # Claude baseline
uv run python scripts/run_evals.py --reasoner claude --judge claude \
  --baseline evals/baseline_claude.json --gate --tolerance 0.05
```

CI (`.github/workflows/evals.yml`) runs on every pull request: the offline gate always, and the
Claude gate once `ANTHROPIC_API_KEY` is a repo secret and `evals/baseline_claude.json` exists.

### Rubric checker (Julia-1 / Jev)

A decision model answers the yes/no checks in `PLAN_RUBRIC` and each check becomes its own score
(`rubric:<check>`), plus `rubric_pass_rate`, the share of checks with P(yes) >= 0.5. Only checks
visible in the plan's text go here; whether the plan is right stays with the LLM judge.

| Check | Asks |
|---|---|
| `names_affected_asset` | Does the plan name the affected table or job? |
| `states_specific_cause` | Does it state a specific root cause, not just the symptom? |
| `single_course_of_action` | Does it commit to one course of action rather than alternatives? |
| `concrete_steps` | Are the next steps concrete actions, not "investigate" or "escalate"? |
| `includes_verification` | Is there a step that confirms the fix worked? |
| `steps_address_cause` | Do the steps act on the stated root cause? |
| `no_unguarded_destructive_action` | Does it avoid deleting or overwriting data without a safeguard? |

| | Julia-1 | Jev |
|---|---|---|
| Runs | Locally on CPU, ~20 ms per call | Hosted by TypeSafe |
| Data | Never leaves the machine | Each plan is sent to TypeSafe |
| Cost | Free | $0.042 per 1M input tokens, shown in the cost report |
| Needs | `--extra julia` and the 577 MB download | `--extra jev` and `TYPESAFE_API_KEY` |

```bash
# Julia-1: the julia package ships inside the checkpoint folder, so download the full repo once
uvx --from huggingface_hub hf download SupersonicLabs/Julia-1 --local-dir ../julia-1-benchmarks/Julia-1
uv run --extra julia python scripts/run_evals.py --checker julia            # or --julia-checkpoint PATH
uv run --extra julia python scripts/run_evals.py --checker julia --limit 2  # smoke

# Jev: one call per row covers every check
export TYPESAFE_API_KEY=...
uv run --extra jev python scripts/run_evals.py --checker jev

# both checkers on the same plans, then compare row by row
uv run --extra julia python scripts/run_evals.py --checker julia
uv run --extra jev python scripts/run_evals.py --checker jev
uv run python scripts/diff_runs.py

# with Claude writing the plans
uv run --extra julia python scripts/run_evals.py --reasoner claude --judge claude --checker julia --limit 2
```

Per-check probabilities and the failed checks for each row are in the run file under
`details.rubric`. Julia-1 vs Jev head to head on classification: `../julia-1-benchmarks/`.

Not trustworthy yet: on the keyword plans Julia-1 swings from 0.0 to 0.97 on the same check
depending on the wording and how much of the incident it is shown. Hand-label the checks on
~50 plans and measure each question before letting `rubric:*` feed the judge or a gate.

### Gotchas

- Keep `--extra julia` / `--extra jev` on every `uv run` that uses a checker. A plain `uv run`
  re-syncs the environment and removes the extras.
- If a different virtualenv is active, uv warns that `VIRTUAL_ENV` does not match and uses this
  project's `.venv` anyway. `deactivate` first to silence it.
- `git-lfs` is not needed: download Julia-1 with the `hf download` command above, not `git clone`.
