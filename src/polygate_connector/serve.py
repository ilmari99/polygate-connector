"""HTTP entry point: the MCP server over Streamable HTTP, hardened for public exposure.

One FastAPI app serves the MCP endpoint at ``/mcp`` (POST/GET/DELETE on a
single route, per the Streamable HTTP transport) plus a ``/healthz`` probe.
Host and Origin headers are validated against ``PUBLIC_HOST`` (DNS-rebinding
protection), and slowapi enforces per-IP and global rate limits at the ASGI
layer - per-route decorators never see traffic that flows into a mounted
sub-app, so the middleware form is load-bearing here.

Intended deployment: uvicorn bound to loopback (or a container-published
port) behind a Cloudflare named tunnel that terminates TLS at the edge.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from mcp.server.transport_security import TransportSecuritySettings
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIASGIMiddleware

from . import __version__, mcp_server
from .config import Settings, get_settings
from .core.errors import ConfigurationError


def _client_ip_factory(trust_proxy_headers: bool):
    """Build the rate-limit key function.

    Behind the tunnel every request's socket address is the local cloudflared,
    so without header awareness the per-IP limit degrades into a global one;
    ``CF-Connecting-IP`` is set by Cloudflare and not spoofable through it.
    On a deployment where clients reach the process directly, header trust
    must be off or the limit becomes spoofable.
    """

    def _client_ip(request: Request) -> str:
        if trust_proxy_headers:
            forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get(
                "X-Forwarded-For", ""
            )
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    return _client_ip


def _rate_limited(_request: Request, exc: RateLimitExceeded) -> Response:
    return Response(
        content='{"error": "rate_limited", "detail": "request rate limit exceeded; retry shortly"}',
        status_code=429,
        media_type="application/json",
    )


def _landing_page(public_host: str) -> str:
    """Human-readable page for a browser that opens the MCP endpoint URL.

    Whoever clicks the connector link should learn the server is up and how
    to use it - not a JSON-RPC "Not Acceptable" error that reads like an
    outage.
    """
    url = f"https://{public_host}/mcp"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymarket Research MCP</title>
<style>
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 40rem;
         margin: 4rem auto; padding: 0 1rem; color: #1a1a1a; }}
  code {{ background: #eee; padding: 0.15rem 0.4rem; border-radius: 4px; }}
  .ok {{ color: #0a7d33; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111; color: #ddd; }}
    code {{ background: #222; }}
    .ok {{ color: #4cc575; }}
    a {{ color: #7ab8ff; }}
  }}
</style>
</head>
<body>
<h1>Polymarket Research MCP</h1>
<p class="ok">&#10003; The server is up (version {__version__}).</p>
<p>This URL is a <a href="https://modelcontextprotocol.io">Model Context
Protocol</a> endpoint: it speaks JSON-RPC to AI clients, not HTML to
browsers, which is why there is nothing more to see here.</p>
<p>To use it in Claude: <b>Settings &rarr; Connectors &rarr; Add custom
connector</b>, then paste</p>
<p><code>{url}</code></p>
<p>Fourteen read-only tools over Polymarket's public prediction-market data:
search, markets, events, order books, price history, top holders, comments.
No account or API key required.</p>
<p><a href="https://github.com/ilmari99/polygate-connector">Source &amp; docs</a> &middot;
<a href="https://github.com/ilmari99/polygate-connector/blob/main/docs/privacy-policy.md">Privacy policy</a> &middot;
<a href="/healthz">Health check</a></p>
</body>
</html>
"""


class _BrowserLanding:
    """Serve the landing page when a browser GETs the MCP endpoint (or ``/``).

    An MCP client's GET always advertises ``Accept: text/event-stream`` -
    that request opens the SSE channel - while a browser's never does, and
    would otherwise receive a bare JSON-RPC "Not Acceptable" error. Protocol
    traffic (every POST/DELETE, and any GET that accepts an event stream)
    passes through untouched.
    """

    def __init__(self, app, html: str):
        self.app = app
        self.html = html.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") in ("GET", "HEAD")
            and scope.get("path", "").rstrip("/") in ("", "/mcp")
        ):
            accept = next(
                (v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"accept"),
                "",
            )
            if "text/event-stream" not in accept:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/html; charset=utf-8"),
                            (b"content-length", str(len(self.html)).encode()),
                            (b"cache-control", b"no-store"),
                        ],
                    }
                )
                body = b"" if scope["method"] == "HEAD" else self.html
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the public-facing ASGI app around the MCP Streamable HTTP app."""
    settings = settings or get_settings()
    public_host = settings.public_host
    if not public_host:
        raise ConfigurationError(
            "PUBLIC_HOST is not set. Set it to the public hostname this server "
            "is reached at (e.g. PUBLIC_HOST=mcp.example.com) so Host/Origin "
            "validation can be pinned to it."
        )

    # Must be assigned before streamable_http_app() builds the transport.
    mcp_server.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, f"{public_host}:443", f"{public_host}:{settings.bind_port}"],
        allowed_origins=[f"https://{public_host}"],
    )
    # A session manager only survives one run(); drop any cached one so
    # create_app() always yields a startable app (uvicorn reload, tests).
    mcp_server.mcp._session_manager = None
    mcp_app = mcp_server.mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_server.service_context():
            async with mcp_server.mcp.session_manager.run():
                yield

    app = FastAPI(title="Polymarket Research MCP", version=__version__, lifespan=lifespan)
    app.state.limiter = Limiter(
        key_func=_client_ip_factory(settings.trust_proxy_headers),
        default_limits=[settings.rate_limit_per_ip],
        application_limits=[settings.rate_limit_global],
    )
    app.add_exception_handler(RateLimitExceeded, _rate_limited)
    app.add_middleware(SlowAPIASGIMiddleware)
    # Outermost (added last): a static info page for humans, served before
    # rate limiting so browser clicks never consume protocol quota.
    app.add_middleware(_BrowserLanding, html=_landing_page(public_host))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.mount("/", mcp_app)
    return app


def run() -> None:
    """Console-script entrypoint: serve over Streamable HTTP."""
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
