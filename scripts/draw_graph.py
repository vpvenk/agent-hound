"""Draw the compiled graph and highlight the path one fake run took. Writes graph.html and opens it."""

import webbrowser
from collections import Counter
from pathlib import Path

from agenthound.harness.graph import build_graph
from run_fake_incident import FAKE_INCIDENT, initial_state

OUT = Path(__file__).resolve().parent.parent / "graph.html"


def run_path(graph) -> list[str]:
    path = ["__start__"]
    for update in graph.stream(initial_state(FAKE_INCIDENT), stream_mode="updates"):
        path.extend(update.keys())
    return path + ["__end__"]


def main():
    graph = build_graph()
    structure = graph.get_graph()
    path = run_path(graph)
    traversed = Counter(zip(path, path[1:]))  # (source, target) -> times taken

    lines = ["graph TD"]
    styles = []
    for i, edge in enumerate(structure.edges):
        arrow = "-.->" if edge.conditional else "-->"
        times = traversed.get((edge.source, edge.target), 0)
        label = f"|x{times}|" if times else ""
        lines.append(f"    {edge.source} {arrow}{label} {edge.target}")
        if times:
            styles.append(f"    linkStyle {i} stroke:#e8590c,stroke-width:3px")
    lines += styles

    steps = "".join(f"<li>{n}</li>" for n in path[1:-1])
    OUT.write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Agenthound graph</title>
<style>body{{font-family:system-ui;margin:2rem;background:#fff;color:#222}} .row{{display:flex;gap:3rem}}</style>
</head><body>
<h2>Agenthound harness</h2>
<p>Dashed = conditional edge (a router decides). <b style="color:#e8590c">Orange</b> = taken in this run, with count.</p>
<div class="row">
  <pre class="mermaid">{chr(10).join(lines)}</pre>
  <div><h3>Run path</h3><ol>{steps}</ol></div>
</div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{startOnLoad: true}});
</script>
</body></html>""")
    print(f"Wrote {OUT}")
    webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
