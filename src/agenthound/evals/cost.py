"""Token + dollar tally per component, so every run prints what it cost.

The doc's note: "every LLM-judge call is a model call, so understand token cost per row
before running the full set." This is that number.
"""

from collections import defaultdict

# $ per 1M tokens (input, output). Anthropic first-party list prices.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "jev": (0.042, 0.00),  # TypeSafe list price; output is free
}

_totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])  # in, out, calls


def record_usage(component: str, model: str, usage) -> None:
    t = _totals[(component, model)]
    t[0] += usage.input_tokens
    t[1] += usage.output_tokens
    t[2] += 1


def report(rows: int) -> str:
    if not _totals:
        return "cost: $0 (no model calls)"
    lines, total = [], 0.0
    for (component, model), (tin, tout, calls) in sorted(_totals.items()):
        pin, pout = PRICES.get(model, (0.0, 0.0))
        dollars = tin / 1e6 * pin + tout / 1e6 * pout
        total += dollars
        lines.append(f"  {component:<10} {model:<18} {calls:>3} calls  {tin:>8} in  {tout:>7} out  ${dollars:.4f}")
    lines.append(f"  total ${total:.4f}   per row ${total / max(rows, 1):.4f}")
    return "cost:\n" + "\n".join(lines)
