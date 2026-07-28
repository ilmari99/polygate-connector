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
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlparse

LINE = re.compile(
    r"tool=(?P<tool>\S+) duration_ms=(?P<ms>\d+) status=(?P<status>\S+)"
    r"(?: bytes=(?P<bytes>\S+) returned=(?P<returned>\S+))?"
)

# Present only when the server runs with LOG_QUERIES=true.
URL_LINE = re.compile(r'HTTP Request: GET (?P<url>https://\S+polymarket\.com\S+)')


def main() -> int:
    calls: dict[str, list[float]] = defaultdict(list)
    sizes: dict[str, list[int]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    statuses: dict[str, int] = defaultdict(int)
    queries: Counter[str] = Counter()
    upstream_paths: Counter[str] = Counter()

    for line in fileinput.input():
        url_match = URL_LINE.search(line)
        if url_match:
            parsed = urlparse(url_match["url"].rstrip('"'))
            upstream_paths[parsed.path] += 1
            for q in parse_qs(parsed.query).get("q", []):
                queries[q] += 1
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

    if queries:
        print("\ntop search queries (LOG_QUERIES=true):")
        for q, n in queries.most_common(15):
            print(f"  {n:>4}  {q}")
    if upstream_paths:
        print("\nupstream paths:")
        for path, n in upstream_paths.most_common():
            print(f"  {n:>4}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
