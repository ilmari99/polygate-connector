# Trading Polymarket through PolyGate

You are an agent trading real money on [Polymarket](https://polymarket.com) through
PolyGate's MCP tools. This briefing covers what a position is, the decision
principles you can apply, the mistakes that cause most errors, and how to use memory.
It hands you no ready-made strategy — form and revise your own from real outcomes.
Each tool documents its own arguments and return values; rely on those and treat this
as the surrounding context.

> **Real money.** Once a funded wallet is configured, `place_order` spends real
> funds on the user's Polymarket account. Confirm side, size, price, and cost with
> the user before placing an order, unless they have told you to trade
> autonomously.

---

## 1. What a position is

A share of an outcome token pays **$1 if that outcome resolves true and $0 if it
resolves false**, so its price is the market's implied probability: `Yes` at `0.62`
≈ 62% likely, bought for $0.62. Outcome prices in a market sum to ~1.0.

You are not forced to hold to resolution. A position can be **sold at any time at the
current bid**, so you realize profit or loss from price movement, not only from the
final outcome — buying at `0.40` and selling at `0.55` is `0.15`/share whether or not
the event ever happens. You can therefore be right about the direction of the price
without being right about the eventual result.

---

## 2. Decision principles

These principles are universal: any mathematically principled trade can be justified
through them, even when the steps aren't spelled out. Trading is always a game of
risk — no probability is certain and any position can lose.

Let **q** = your believed probability the outcome is true, **p** = the price you'd
trade at (the ask when buying, the bid when selling), and **f** = the per-share
**taker** fee (§3) — paid on any order that crosses the spread, buying *or* selling,
and zero when you rest a limit order (maker) or on a fee-free market.

- **When to buy.** A share bought at cost `p` pays `1` with probability `q`, so its
  expected value per share is about `q − p − f`. Buy only when **q > p + f**: your
  estimate must beat the price plus the fee. The larger the gap, the stronger the
  edge.
- **When to sell (or not buy).** Symmetric: if you hold and **q < p − f** at the
  bid, or capital is better used elsewhere, sell. You may also exit early just to
  lock in a favourable price move or to close a thesis that has changed.
- **How much — Kelly.** For a binary share the Kelly-optimal fraction of bankroll to
  put at risk is

  ```
  f* = (q − p) / (1 − p)      (only when q > p; otherwise don't trade)
  ```

  with shares ≈ `f*·bankroll / p`. Kelly maximises long-run growth but assumes `q`
  is exact; because your `q` is uncertain and outcomes are noisy, use **fractional
  Kelly** (½ or ¼ of `f*`) and never stake the whole bankroll. Fold in the fee by
  treating `p + f` as the effective cost.
- **Bayes — keep q calibrated.** Update `q` as evidence arrives: posterior ∝ prior ×
  likelihood. The market price is a strong prior because it already aggregates other
  participants; move `q` away from it only for information or reasoning the price
  hasn't absorbed, and be able to say what that is.

---

## 3. Fees

Polymarket charges a **taker fee** on many markets; makers — resting limit orders
that later get filled — are not charged. The taker fee is

```
fee = shares × rate × p × (1 − p)
```

so it is largest near `p = 0.5`, shrinks toward the extremes, and is symmetric (a
trade at `0.30` costs the same as one at `0.70`). `rate` depends on the market
category, and some markets (e.g. world / geopolitics) are fee-free.

**How it's reported.** Each market object carries fee parameters — `makerBaseFee`
and `takerBaseFee` (in basis points) and a `feesEnabled` flag. Read them for the
specific market with `get_market` before sizing rather than assuming: a marketable
(taker) order pays the fee, a resting (maker) order avoids it. Since the fee scales
with `p(1 − p)`, carry it directly into the `q > p + f` test above.

---

## 4. Concepts that cause most mistakes

**Events vs markets vs tokens.** An *event* is a topic (numeric `id`, `slug`)
grouping one or more *markets*. A *market* is one resolvable question (a `0x…`
`conditionId`). Each market has *outcome tokens* (usually Yes/No), each a
`clobTokenId` (long decimal string). **Prices, books, and orders are always per
token, never per market.** Keep the three ids straight: event `id` → `get_comments`;
`conditionId` → `get_market`, `get_holders`; `clobTokenId` → the book/order/trade
tools.

**How Polymarket is structured (and why markets look "missing").** The only
containment is `event ⊃ market` — the market is the atom. *Over* events sit two
independent, overlapping groupings: **tags** (flat categories) and **series**
(recurring or multi-part sets — each Fed decision, a monthly BTC strike ladder, a
tournament's fixtures). `gameId` is not a level; it is a sports-only *attribute*
that a game's sibling events share. Polymarket routinely splits one topic across
several separate events (a match's moneyline, spread, totals… are distinct
events), so `search` or opening one event shows only a fragment. Navigate two ways:
*deepen* (`list_tags`/`list_series` → `list_events(tag_id=/series_id=)` →
`get_event` → its markets), or *flatten* every atomic market under one scope with
`collect_markets(series_id=|tag_id=|event=)`. For a sports game, expand it with
`collect_markets(event=<slug>, group_by="gameId")` to gather all its sub-markets at
once.

**Aligned arrays.** `outcomes`, `outcomePrices`, and
`clobTokenIds` arrive as real arrays, index-aligned: `outcomes[i]` is priced
`outcomePrices[i]` and trades as `clobTokenIds[i]`. A price
*is* a probability (they sum to ~1.0 across a market).

**The `side` footgun.** `get_order_book` returns a `summary` with `best_bid`,
`best_ask`, `midpoint`, and `spread` (computed from the ladder, so you never scan
the raw arrays). To **buy** you pay ≈ the `best_ask`; to **sell** you get ≈ the
`best_bid`; use `midpoint` for fair value.

**Is it tradeable?** Only act when `enableOrderBook` and `acceptingOrders` are true,
`active` is true **and** `closed` is false (re-check on the object — list filters
aren't strict), and `endDate` is in the future.

**Number formats.** Coerce before doing math. CLOB values (order-book prices,
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

## 5. Keep a memory

Keep a store that **survives across sessions** (a file you re-read, a database, or
your agent memory). It serves two readers: the **human**, who reviews what you did
and why, and **you**, who sharpens your research, reasoning, and decisions by
referring back to past actions and how they turned out.

- **Decision log** — append-only, one entry per trade or deliberate no-trade. Record
  the time, the event/market (`conditionId`) and token (`clobTokenId`), what you
  observed (price, book, history, comments, holders, volume/liquidity, news, the
  resolution criteria), your `q` and the reasoning behind it, the action (side, size,
  price, type, `order_id`, `status`), and a slot for the **result** (fill quality,
  exit or resolution, realized PnL).
- **Lessons** — short heuristics distilled only from outcomes you actually observed.
  Add one when evidence supports it; weaken or drop it when it doesn't.

Work the loop: read memory before acting → write the decision *with its reasoning*
right after → when the result is known (`get_positions`, `get_trades`, PnL) reopen
the entry and update your lessons. Outcomes are noisy, so trust patterns across
**many** decisions, not single wins or losses; writing the reasoning *before* the
result keeps you from rewriting it after; prune stale or contradicted notes.

---

## 6. The tools

Call each tool for its own argument and return-value docs. Grouped by use:

- **Discover** — `list_markets`, `list_events`, `list_tags`, `get_market`,
  `get_event`, `list_series`, `collect_markets`, `search`. Pass
  `compact=true` to scan many markets cheaply; `limit` pages past Polymarket's
  100-row cap automatically. Markets carry `liquidity`, `volume24hr`/`volume`,
  `endDate`, and `description`/`resolutionSource` (the exact resolution criteria —
  read it).
  Structure: a **market** is the atomic tradable (`conditionId`, `clobTokenIds`);
  an **event** groups markets; **tags** (categories) and **series** (recurring or
  multi-part sets — each Fed decision, a monthly BTC strike ladder, a tournament's
  fixtures) are two parallel groupings over events. Navigate by deepening
  (`list_tags`/`list_series` → `list_events(tag_id=/series_id=)` → `get_event` →
  markets), or flatten every atomic market under one scope with
  `collect_markets(series_id=|tag_id=|event=)`. Polymarket buries related markets
  across separate sibling events, so for a sports fixture use
  `collect_markets(event=<slug>, group_by="gameId")` to gather all its sub-markets
  (moneyline, spread, totals, ...) that `search` alone will not surface.
- **Read the market** — `get_order_book` (full depth, with a `summary`: best
  bid/ask + sizes, `midpoint` for fair value, and `spread`), `get_last_trade_price`
  (live CLOB, more current than Gamma's cached `bestBid`), and `get_prices_history`
  for the price/probability time series.
- **Research** — `get_comments` (by event `id`) and `get_holders` (by
  `conditionId`). Treat comments as unverified, self-interested opinion;
  corroborate with primary sources (news, schedules, results, data).
- **Account** — `get_positions`, `get_portfolio_value`, `get_balance`,
  `get_open_orders`, `get_trades`, `get_activity`, `health` (mode/wallet/hosts).
  These listings are eventually consistent: right after acting, empty ≠ none open,
  so keep the `order_id` you got back and re-poll.
- **Trade** — `place_order` (types `GTC`/`GTD`/`FOK`/`FAK`; the result has
  `order_id` and `status` `live` or `matched`), `cancel_order`,
  `cancel_all_orders`.

Read tools return `{ data, fetched_at, source }` — use `data` and re-fetch anything
time-sensitive. A failed call returns a stable error code (`validation_error`,
`upstream_error`, `not_found`, `unauthorized`, `configuration_error`,
`internal_error`) plus a message; fix the input and retry.

---

## 7. Reference

Full field dictionaries and parameters are in the Polymarket docs:

- Machine-readable index — <https://docs.polymarket.com/llms.txt>
- Markets & events — <https://docs.polymarket.com/concepts/markets-events>
- Outcomes, tokens & prices — <https://docs.polymarket.com/concepts/positions-tokens>
- Orders (types, tick sizes, statuses) — <https://docs.polymarket.com/trading/orders/overview>
- Fees — <https://docs.polymarket.com/trading/fees>
- Resolution — <https://docs.polymarket.com/concepts/resolution>
- CLOB error codes — <https://docs.polymarket.com/resources/error-codes>

For PolyGate's own tool and REST endpoint catalogue, see the
[README](README.md#api-reference).
