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
