"""Measure default-argument payload sizes for every read-only tool.

Calls each MCP tool function against the live Polymarket APIs with default
arguments - so the measured size is the true on-the-wire result, including
markdown-table rendering and the response cap - and prints a table of
serialized bytes and approximate tokens (bytes / 4). Ids needed by detail
tools (conditionId, event slug, token id) are discovered with unmeasured
facade calls first, so the script is self-contained.

With ``--save-fixtures DIR`` every raw upstream response made by a measured
tool call is snapshotted as ``DIR/<tool>.json`` (a list of ``{url, params,
response}`` records in call order) for use as offline respx fixtures.

Usage:
    python scripts/measure_payloads.py [--save-fixtures tests/fixtures]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from polygate_connector import mcp_server
from polygate_connector.config import get_settings
from polygate_connector.services.facade import PolymarketService


def _size(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str))


class Recorder:
    """Tees every HttpClient.get_json response, bucketed per tool."""

    def __init__(self, service: PolymarketService) -> None:
        self.current_tool: str | None = None
        self.records: dict[str, list[dict[str, Any]]] = {}
        self._orig = service._http.get_json
        service._http.get_json = self._wrapped  # type: ignore[method-assign]

    async def _wrapped(self, url: str, **kwargs: Any) -> Any:
        data = await self._orig(url, **kwargs)
        if self.current_tool is not None:
            self.records.setdefault(self.current_tool, []).append(
                {"url": url, "params": kwargs.get("params") or {}, "response": data}
            )
        return data

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for tool, records in self.records.items():
            path = directory / f"{tool}.json"
            path.write_text(json.dumps(records, indent=2, default=str))
            print(f"  fixture: {path} ({len(records)} request(s))", file=sys.stderr)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-fixtures", metavar="DIR", default=None)
    args = parser.parse_args()

    service = PolymarketService(get_settings())
    recorder = Recorder(service)
    mcp_server._service = service
    rows: list[tuple[str, int | str]] = []

    async def run(tool: str, coro_factory) -> None:
        recorder.current_tool = tool
        try:
            result = await coro_factory()
            rows.append((tool, _size(result)))
        except Exception as exc:  # noqa: BLE001 - live APIs; report and move on
            rows.append((tool, f"ERROR: {type(exc).__name__}: {exc}"))
        finally:
            recorder.current_tool = None

    try:
        # Discovery (unmeasured, unrecorded): find real ids to feed the tools.
        condition_id = event_slug = event_id = token_id = None
        try:
            markets = await service.list_markets(limit=10, verbosity="compact")
            for market in markets.rows if isinstance(markets.rows, list) else []:
                tokens = market.get("clobTokenIds")
                if market.get("conditionId") and isinstance(tokens, list) and tokens:
                    condition_id, token_id = market["conditionId"], tokens[0]
                    break
            events = await service.list_events(limit=10, verbosity="compact")
            for event in events.rows if isinstance(events.rows, list) else []:
                if event.get("slug") and event.get("id"):
                    event_slug, event_id = event["slug"], event["id"]
                    break
        except Exception as exc:  # noqa: BLE001
            print(f"discovery failed: {exc}", file=sys.stderr)

        await run("health", lambda: mcp_server.health())
        await run("list_markets", lambda: mcp_server.list_markets())
        await run("list_events", lambda: mcp_server.list_events())
        await run("search", lambda: mcp_server.search("election"))
        await run("list_series", lambda: mcp_server.list_series())
        await run("list_tags", lambda: mcp_server.list_tags())

        if condition_id:
            await run("get_market", lambda: mcp_server.get_market(condition_id))
            await run("get_holders", lambda: mcp_server.get_holders(condition_id))
        else:
            rows.append(("get_market", "SKIPPED: no conditionId discovered"))
            rows.append(("get_holders", "SKIPPED: no conditionId discovered"))
        if event_slug:
            await run("get_event", lambda: mcp_server.get_event(event_slug))
            await run("collect_markets", lambda: mcp_server.collect_markets(event=event_slug))
            await run("get_comments", lambda: mcp_server.get_comments(int(event_id)))
        else:
            rows.append(("get_event", "SKIPPED: no event slug discovered"))
            rows.append(("collect_markets", "SKIPPED: no event slug discovered"))
            rows.append(("get_comments", "SKIPPED: no event id discovered"))
        if token_id:
            await run("get_order_book", lambda: mcp_server.get_order_book(token_id))
            await run("get_last_trade_price", lambda: mcp_server.get_last_trade_price(token_id))
            await run("get_prices_history", lambda: mcp_server.get_prices_history(token_id))
        else:
            rows.append(("get_order_book", "SKIPPED: no token id discovered"))
            rows.append(("get_last_trade_price", "SKIPPED: no token id discovered"))
            rows.append(("get_prices_history", "SKIPPED: no token id discovered"))
    finally:
        mcp_server._service = None
        await service.aclose()

    name_w = max(len(name) for name, _ in rows)
    print(f"{'tool':<{name_w}}  {'bytes':>9}  {'~tokens':>8}")
    print(f"{'-' * name_w}  {'-' * 9}  {'-' * 8}")
    total = 0
    for name, size in sorted(rows, key=lambda r: -(r[1] if isinstance(r[1], int) else 0)):
        if isinstance(size, int):
            total += size
            print(f"{name:<{name_w}}  {size:>9,}  {size // 4:>8,}")
        else:
            print(f"{name:<{name_w}}  {size}")
    print(f"{'-' * name_w}  {'-' * 9}  {'-' * 8}")
    print(f"{'TOTAL':<{name_w}}  {total:>9,}  {total // 4:>8,}")

    if args.save_fixtures:
        recorder.save(Path(args.save_fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
