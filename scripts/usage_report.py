"""Aggregate tool-usage statistics from server logs.

Reads log lines (stdin or files) containing the per-call record emitted by
``_run_tool``::

    ... tool=list_markets duration_ms=42 status=ok bytes=2097 returned=10

and prints per-tool call counts, error rates, latency, and payload sizes.
The logs contain no arguments, results, or IPs, so this report is exactly
the usage-pattern data the privacy policy permits collecting.

Usage:
    python scripts/usage_report.py server.log
    docker logs polygate-connector 2>&1 | python scripts/usage_report.py
"""

from __future__ import annotations

import fileinput
import re
import statistics
import sys
from collections import defaultdict

LINE = re.compile(
    r"tool=(?P<tool>\S+) duration_ms=(?P<ms>\d+) status=(?P<status>\S+)"
    r"(?: bytes=(?P<bytes>\S+) returned=(?P<returned>\S+))?"
)


def main() -> int:
    calls: dict[str, list[float]] = defaultdict(list)
    sizes: dict[str, list[int]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    statuses: dict[str, int] = defaultdict(int)

    for line in fileinput.input():
        match = LINE.search(line)
        if not match:
            continue
        tool = match["tool"]
        calls[tool].append(float(match["ms"]))
        if match["status"] != "ok":
            errors[tool] += 1
            statuses[match["status"]] += 1
        if match["bytes"] and match["bytes"].isdigit():
            sizes[tool].append(int(match["bytes"]))

    if not calls:
        print("no tool-call log lines found", file=sys.stderr)
        return 1

    name_w = max(len(t) for t in calls)
    print(f"{'tool':<{name_w}}  {'calls':>6}  {'errors':>6}  {'p50 ms':>7}  {'p95 ms':>7}  {'med bytes':>9}")
    print(f"{'-' * name_w}  {'-' * 6}  {'-' * 6}  {'-' * 7}  {'-' * 7}  {'-' * 9}")
    for tool in sorted(calls, key=lambda t: -len(calls[t])):
        durations = sorted(calls[tool])
        p50 = statistics.median(durations)
        p95 = durations[max(0, int(len(durations) * 0.95) - 1)]
        med_bytes = int(statistics.median(sizes[tool])) if sizes[tool] else 0
        print(
            f"{tool:<{name_w}}  {len(durations):>6}  {errors[tool]:>6}  "
            f"{p50:>7.0f}  {p95:>7.0f}  {med_bytes:>9,}"
        )
    total = sum(len(v) for v in calls.values())
    print(f"\n{total} calls, {sum(errors.values())} errors", end="")
    if statuses:
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
        print(f" ({breakdown})", end="")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
