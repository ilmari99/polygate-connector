# Reviewer guide - Polymarket Research MCP

Step-by-step instructions for functionally testing this connector. No
Polymarket knowledge is assumed.

## What this is

Polymarket is a prediction market: each market is a question (for example,
"Will X win the 2026 election?") whose Yes/No shares trade between $0 and
$1, so a share's price reads as the market's probability estimate. This
connector exposes that public data - markets, prices, order books, holders,
comments - **read-only**. There is no way to trade, no account, and no user
data involved.

## Setup

No test account is needed - the connector has no authentication.

1. In Claude: Settings → Connectors → **Add custom connector**.
2. URL: `https://mcp.polymarket4ai.com/mcp`.
3. Authentication: none.

## Test prompts (increasing depth)

1. **Search and list** - exercises `search`, `list_markets`:
   > What are the highest-volume Polymarket markets about the 2026 US
   > midterms?

2. **Drill into an event** - exercises `get_event`, `get_market`,
   `get_order_book`:
   > Pick the leading market from those results, show me its exact
   > resolution status and current prices, and the order book for the
   > leading outcome.

3. **Full research pass** - exercises `get_prices_history`, `get_holders`,
   `get_comments`, `collect_markets`, `list_series`, `list_tags`:
   > How has that outcome's price moved over the past week? Who are the
   > largest holders on each side, what is the comment section saying, and
   > are there related markets in the same series I should know about?

`health` and `get_last_trade_price` round out the 14 tools; asking "is the
data source healthy, and what was the very last traded price?" covers both.

## Error behaviour to verify

Invalid input returns a typed, actionable error - never a bare 500 or a
traceback. For example, asking for a market with a made-up id yields
`not_found` with a hint to use `search`; calling `collect_markets` without a
scope yields `validation_error` naming the required parameters.

## Expected response shape

List results arrive as one small page (markdown table or rows) with
`next_offset`/`next_page` when more data exists, and every response stays
under 100KB with an explicit notice if it was reduced.
