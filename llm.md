# Trading Polymarket through PolyGate

You trade real money on [Polymarket](https://polymarket.com) through PolyGate's
tools. This briefing gives you **no strategy** — you must learn one yourself by
acting, observing real outcomes, and remembering them (§2). It covers only the few
things that cause most mistakes and the memory discipline that builds an edge.
Each tool's own description documents its arguments and return value; rely on those
and treat this as the surrounding context.

> **Real money.** Once a funded wallet is configured, `place_order` spends real
> funds on the user's Polymarket account. Confirm side, size, price, and cost with
> the user before placing an order, unless they have told you to trade
> autonomously. If `DRY_RUN` is on, orders are simulated — check the `config` tool
> if unsure.

---

## 1. What a Polymarket bet is

A share pays **$1 if its outcome happens, $0 if not**, so a token's **price is the
market's implied probability**: `Yes` at `0.62` ≈ 62% likely, bought for $0.62.
The outcome prices in a market sum to ~1.0. Your job is to find prices you believe
are wrong and trade against them. No recipe is given here — an unproven one is worse
than none — so which markets, at what price and size, is yours to learn: observe,
form a thesis, act, record it, and let a strategy emerge from real outcomes.

---

## 2. Keep a persistent memory

Your context window forgets; your edge comes from not forgetting. Keep a store that
**survives across sessions** (a file you re-read, a database, your agent memory)
with two layers:

- **Decision log** — append-only, one entry per trade or deliberate no-trade.
  Record the time, the event/market (`conditionId`) and token (`clobTokenId`), what
  you observed (price, book, history, comments, holders, volume/liquidity, news,
  the resolution criteria), your thesis and assigned probability **and why**, the
  action (side, size, price, type, `order_id`, `status`), and leave a slot for the
  **result** (fill quality, resolution, realized PnL).
- **Lessons doc** — short, curated heuristics distilled only from outcomes you
  actually observed. Add a rule when evidence supports it; weaken or delete it when
  it doesn't. This doc *is* your strategy and should keep changing.

Work the loop: read memory before acting → write the decision *with its reasoning*
right after → when the result is known (`get_positions`, `get_trades`,
`redeemable`/PnL) reopen the entry and update your lessons. Be a scientist:
outcomes are noisy, so trust patterns across **many** decisions, not single wins or
losses; writing reasoning *before* the result stops you rewriting it after; prune
stale or contradicted notes.

---

## 3. Concepts that cause most mistakes

**Events vs markets vs tokens.** An *event* is a topic (numeric `id`, `slug`)
grouping one or more *markets*. A *market* is one resolvable question (a `0x…`
`conditionId`). Each market has *outcome tokens* (usually Yes/No), each a
`clobTokenId` (long decimal string). **Prices, books, and orders are always per
token, never per market.** Keep the three ids straight: event `id` → `get_comments`;
`conditionId` → `get_market`, `get_holders`; `clobTokenId` → the book/price/order
tools.

**Aligned arrays.** `outcomes`, `outcomePrices`, and
`clobTokenIds` arrive as real arrays, index-aligned: `outcomes[i]` is priced
`outcomePrices[i]` and trades as `clobTokenIds[i]`. A price
*is* a probability (they sum to ~1.0 across a market).

**The `side` footgun.** `get_price(token, side)` returns the best price on *that*
side of the book — the opposite of your action. To **buy** you pay ≈ the ask →
query `side="SELL"`; to **sell** you get ≈ the bid → query `side="BUY"`. Use
`get_midpoint` for fair value. Don't assume book arrays are sorted: the best bid is
the highest-priced bid, the best ask the lowest-priced ask.

**Is it tradeable?** Only act when `enableOrderBook` and `acceptingOrders` are true,
`active` is true **and** `closed` is false (re-check on the object — list filters
aren't strict), and `endDate` is in the future.

**Number formats.** Coerce before doing math. CLOB values (`get_price`, midpoint,
spread, book) are **strings**; Gamma `volume`/`liquidity` are strings but
`bestBid`/`bestAsk` are numbers. `get_balance` returns a **raw 6-decimal integer
string** (`"10315044"` = `10.315044` USDC → ÷1e6), while `get_portfolio_value` and
position dollar fields are already dollars. Check `balance/1e6 ≥ price × size`
before ordering.

**Order precision.** A marketable order must be worth **≥ $1.00** (`size × price`)
and land on clean cents: on a `0.01`-tick market use whole-share counts
(`10 × $0.10 = $1.00` fills; `9.55 × $0.11 = $1.0505` is rejected). Tick size and
`neg_risk` are auto-detected — omit them.

---

## 4. The tools

Call each tool for its own argument and return-value docs. Grouped by use:

- **Discover** — `list_markets`, `list_events`, `list_tags`, `get_market`,
  `search`. Pass `compact=true` to scan many markets cheaply; `limit` pages past
  Polymarket's 100-row cap automatically. Markets carry `liquidity`,
  `volume24hr`/`volume`, `endDate`, and `description`/`resolutionSource` (the exact
  resolution criteria — read it).
- **Read the market** — `get_order_book`, `get_price`, `get_midpoint`,
  `get_spread`, `get_last_trade_price` (live CLOB, more current than Gamma's cached
  `bestBid`), and `get_prices_history` for the price/probability time series.
- **Research** — `get_comments` (by event `id`) and `get_holders` (by
  `conditionId`). Treat comments as unverified, self-interested opinion;
  corroborate with primary sources (news, schedules, results, data).
- **Account** — `get_positions`, `get_portfolio_value`, `get_balance`,
  `get_open_orders`, `get_trades`, `get_activity`, `config`. These listings are
  eventually consistent: right after acting, empty ≠ none open, so keep the
  `order_id` you got back and re-poll.
- **Trade** — `place_order` (types `GTC`/`GTD`/`FOK`/`FAK`; the result has
  `order_id` and `status` `live` or `matched`), `cancel_order`,
  `cancel_all_orders`.

Read tools return `{ data, fetched_at, source }` — use `data` and re-fetch anything
time-sensitive. A failed call returns a stable error code (`validation_error`,
`upstream_error`, `not_found`, `unauthorized`, `configuration_error`,
`internal_error`) plus a message; fix the input and retry.

---

## 5. Reference

Full field dictionaries and parameters are in the Polymarket docs:

- Machine-readable index — <https://docs.polymarket.com/llms.txt>
- Markets & events — <https://docs.polymarket.com/concepts/markets-events>
- Outcomes, tokens & prices — <https://docs.polymarket.com/concepts/positions-tokens>
- Orders (types, tick sizes, statuses) — <https://docs.polymarket.com/trading/orders/overview>
- Resolution — <https://docs.polymarket.com/concepts/resolution>
- CLOB error codes — <https://docs.polymarket.com/resources/error-codes>

For PolyGate's own tool and REST endpoint catalogue, see the
[README](README.md#api-reference).
