# Privacy policy - Polymarket Research MCP

*Last updated: 2026-07-28*

**Summary: this connector requires no account and stores no user data.**

## What we collect

Nothing about you. The connector has no accounts, no authentication, and no
user profiles. Tool queries (for example, a search term or a market id) are
forwarded to Polymarket's public APIs to produce the answer and are not
recorded by us.

## Operational logs

For reliability and usage monitoring we keep logs containing only: the tool
name invoked, the response time, the outcome status, the response size, and
the number of rows returned. Logs never contain query arguments, results, or
personal data. Client IP addresses are used
transiently in memory for rate limiting and are not written to logs. Logs
are retained for up to 7 days and then deleted.

## Third parties

Your queries reach **Polymarket's public APIs** (gamma-api.polymarket.com,
clob.polymarket.com, data-api.polymarket.com), operated by Polymarket, whose
own privacy policy applies to that traffic:
<https://polymarket.com/privacy>. If you connect through Claude, Anthropic's
privacy policy governs the Claude side of the conversation. Public traffic
to this server transits **Cloudflare** (TLS termination and DDoS
protection), subject to Cloudflare's privacy policy. We share nothing with
any other party, and we sell nothing.

## Data storage

We store no user data, so there is nothing to access, correct, export, or
delete. Short-lived in-memory caches of public market data (seconds to
minutes) exist purely for performance.

## Changes

Changes to this policy are published at this URL with an updated date.

## Contact

- GitHub Issues: <https://github.com/ilmari99/polygate-connector/issues>
- Email: <i.vahteristo@gmail.com>
