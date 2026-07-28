# Polymarket Research MCP

**Read-only [Polymarket](https://polymarket.com) prediction-market data for
Claude and any MCP host.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Fourteen read-only tools over Polymarket's public APIs: search, market and
event listings, order books, prices, price history, top holders, and
comments. No account, wallet, or API key - the server holds no user data at
all.

This is the connector-directory fork of [polygate](https://github.com/ilmari99/polygate),
with every trading and account capability removed at the source level (not
merely disabled). If you want to trade Polymarket from an LLM with your own
wallet, use upstream `polygate` instead.

> **Not financial advice.** This server returns public market data. Nothing it
> returns is investment advice or a recommendation to trade. See
> [DISCLAIMER.md](DISCLAIMER.md).

## Add to Claude

Settings → Connectors → **Add custom connector**, then enter the server URL:

```
https://<your-host>/mcp
```

No authentication is required. Ask Claude things like:

1. *"What are the highest-volume Polymarket markets about the 2026 US midterms?"*
2. *"Show me the order book and recent price history for the leading outcome."*
3. *"Who are the top holders on each side, and what does the comment section think?"*

## Tools

| Tool | What it returns |
|---|---|
| `search` | Full-text search over events, one row per event |
| `list_markets` | A page of markets (question, prices, volume, `conditionId`) |
| `get_market` | One market by `conditionId`: status, outcomes, prices, token ids |
| `list_events` | A page of events with market counts and top markets |
| `get_event` | One event (by slug or id) with its complete market list |
| `list_series` | Catalog of recurring event series (Fed decisions, leagues, ...) |
| `list_tags` | The category tags listings can filter by |
| `collect_markets` | Every market under one series / tag / event, flattened |
| `get_order_book` | Best bid/ask, spread, midpoint, top 5 levels with depth |
| `get_last_trade_price` | Live last-traded price for an outcome token |
| `get_prices_history` | Price history, summarized (raw points on request) |
| `get_holders` | Top holders per outcome for a market |
| `get_comments` | Public comments on an event |
| `health` | Server status and upstream hosts |

Every tool is annotated read-only. List tools return one small page by
default (with `next_offset` to page on) and render as compact markdown
tables; `verbosity="compact"` or `"full"` returns progressively fuller JSON.
Responses are capped at 100KB, always with an explicit truncation notice.
The `polymarket://data-model` resource documents the entity hierarchy and
field glossary.

## Run locally over stdio

```json
{
  "mcpServers": {
    "polymarket-research": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ilmari99/polygate-claude-connector", "polygate-connector-stdio"]
    }
  }
}
```

## Self-host over HTTPS

The HTTP entry point serves Streamable HTTP at `/mcp` with DNS-rebinding
protection and per-IP rate limiting built in. It needs one setting:
`PUBLIC_HOST`, the public hostname requests will arrive with.

```bash
pip install .
PUBLIC_HOST=mcp.example.com polygate-connector   # binds 127.0.0.1:8765
```

Or containerized, with the hardened flags:

```bash
docker build -t polygate-connector .
docker run --rm --read-only --tmpfs /tmp --cap-drop=ALL \
  --security-opt=no-new-privileges \
  -p 127.0.0.1:8765:8765 -e PUBLIC_HOST=mcp.example.com polygate-connector
```

Expose it with a [Cloudflare named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(no inbound ports, TLS at the edge, stable hostname):

```bash
cloudflared tunnel login
cloudflared tunnel create polygate-mcp
cloudflared tunnel route dns polygate-mcp mcp.example.com
cloudflared tunnel run --url http://127.0.0.1:8765 polygate-mcp
```

Your connector URL is then `https://mcp.example.com/mcp`. Verify from outside
your own network before sharing it.

## Configuration

All configuration is by environment variable (or a local `.env`); there are
no secrets.

| Variable | Default | Meaning |
|---|---|---|
| `PUBLIC_HOST` | *(unset)* | Public hostname; required for HTTP serving |
| `BIND_HOST` / `BIND_PORT` | `127.0.0.1` / `8765` | Where uvicorn listens |
| `RATE_LIMIT_PER_IP` | `300/minute` | Loose per-client backstop (connector traffic shares egress IPs) |
| `RATE_LIMIT_GLOBAL` | `600/minute` | slowapi whole-server limit - the binding one |
| `TRUST_PROXY_HEADERS` | `true` | Use `CF-Connecting-IP`/`X-Forwarded-For` as the client IP; disable if clients reach the process directly |
| `UPSTREAM_CONCURRENCY` | `8` | Max in-flight requests to Polymarket |
| `HTTP_TIMEOUT_SECONDS` / `HTTP_MAX_RETRIES` | `15` / `3` | Outbound HTTP behaviour |
| `GAMMA_HOST` / `CLOB_HOST` / `DATA_HOST` | Polymarket production | Upstream API hosts |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

Upstream responses are cached briefly in-process (45s for catalog listings,
5min for search, 15s for live order-book data) to stay fast and polite to
Polymarket's APIs.

## Data handling

No accounts, no user data, no query logging. The server keeps operational
logs of tool name, duration, and status only - never arguments, results, or
IP addresses beyond transient rate limiting. Queries are forwarded to
Polymarket's public APIs. Full policy: [docs/privacy-policy.md](docs/privacy-policy.md).

## Support

Open an issue: <https://github.com/ilmari99/polygate-claude-connector/issues>.
Reviewer/test instructions live in [docs/reviewer-guide.md](docs/reviewer-guide.md).

## Development

```bash
pip install -e '.[dev]'
pytest                                   # offline; includes payload budgets
python scripts/measure_payloads.py       # live payload size report
```

CI fails if any trading symbol, wallet variable, or signing dependency
reappears under `src/`.

## License

MIT - see [LICENSE](LICENSE).
