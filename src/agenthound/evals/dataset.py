import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "incidents.json"


def load_fixtures(path: Path = FIXTURES) -> list[dict]:
    return json.loads(path.read_text())


def to_eval_rows(fixtures: list[dict]) -> list[dict]:
    # Braintrust row shape: input goes to the task; expected only ever reaches the scorers.
    # probes are for the judge-calibration script, not for the task or the scorers.
    return [
        {"input": f["input"], "expected": f["expected"], "metadata": {"id": f["id"], **f["metadata"]}}
        for f in fixtures
    ]
