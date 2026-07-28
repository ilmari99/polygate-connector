# Polymarket data model

Entity hierarchy:

```
tag (category) ─┐
series ─────────┼─> event ─> market ─> outcome token (Yes / No)
```

- **tag** - a flat category (`id`, `label`, `slug`). Filter listings with `tag_id`.
- **series** - a recurring or multi-part set of events: each Fed decision, a
  monthly BTC strike ladder, a tournament's fixtures. Filter with `series_id`.
- **event** - a "market page" grouping one or more markets. Keyed by numeric
  `id` or by `slug`. Sports events also carry a `gameId` shared by the sibling
  events of one fixture.
- **market** - the atomic question, keyed by `conditionId` (0x-prefixed hash).
- **outcome token** - one tradable side of a market, keyed by `clobTokenId`
  (long decimal string). Order books and prices attach to outcome tokens,
  never to markets.

## Field glossary

| Field | Meaning |
|---|---|
| `outcomes` | Outcome names, e.g. `["Yes", "No"]` |
| `outcomePrices` | Prices per outcome; a price is the implied probability |
| `clobTokenIds` | Outcome token ids; index-aligned with `outcomes` and `outcomePrices` |
| `conditionId` | Market id; the key for `get_market` and `get_holders` |
| `liquidityNum` / `volumeNum` / `volume24hr` | Liquidity and traded volume in USD |
| `bestBid` / `bestAsk` / `spread` | Top of the order book (Gamma's cached copy) |
| `active` / `closed` / `acceptingOrders` | Market lifecycle status |
| `endDate` | When the market's question is scheduled to resolve |
| `market_count` / `top_markets` | On event list rows: how many markets the event holds, and its most liquid ones |
| `next_offset` / `next_page` | Present on a list page when more rows exist upstream |
| `truncated` | The result is not the complete set (clamped limit or size cap) |

CLOB values (prices, sizes, spreads) are strings; convert before arithmetic.

## Worked example

1. `search(q="fed rate cut")` - rows of matching events with slugs.
2. `get_event("fed-decision-in-september")` - the event with its complete
   market list, each market carrying `conditionId` and `clobTokenIds`.
3. `get_market("0x1234...")` - one market's status, outcomes, and prices.
4. `get_order_book("21742633...")` - that outcome token's bid/ask summary and
   top price levels.
5. `get_prices_history("21742633...")` - the token's price over the past week,
   summarized.
