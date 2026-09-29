import argparse
import json
import time

from agenthound.harness.graph import build_graph
from agenthound.harness.state import new_case_file

FAKE_INCIDENT = {
    "tenant_id": "tenant-demo",
    "signal": "row_count_anomaly",
    "table": "daily_revenue",
}


def initial_state(incident: dict) -> dict:
    return new_case_file(incident)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.8, help="seconds to pause after each node")
    args = parser.parse_args()

    graph = build_graph()
    plan = None

    # stream_mode="updates" yields {node_name: what_that_node_returned} as each node finishes
    for step, update in enumerate(graph.stream(initial_state(FAKE_INCIDENT), stream_mode="updates"), start=1):
        for node, changes in update.items():
            print(f"\n[{step}] {node}")
            for field, value in changes.items():
                print(f"    {field}: {json.dumps(value)}")
            if node == "write_plan":
                plan = changes["resolution_plan"]
        time.sleep(args.delay)

    print("\n" + "=" * 60 + "\nPLAN\n" + "=" * 60)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
