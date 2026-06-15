"""FastAPI application factory, error handling, and entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import routes_market, routes_portfolio, routes_system, routes_trading
from .config import Settings, get_settings
from .core.errors import PlatformError
from .core.logging import configure_logging, log
from .services.facade import PolymarketService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    _startup_checks(settings)
    app.state.service = PolymarketService(settings)
    suffix = "  [DRY_RUN: orders simulated]" if settings.dry_run else ""
    log.info("PolyGate ready; wallet=%s%s", settings.funder_address, suffix)
    try:
        yield
    finally:
        await app.state.service.aclose()
        log.info("Platform stopped.")


def _startup_checks(settings: Settings) -> None:
    """Fail fast on insecure or inconsistent configuration."""
    if settings.platform_api_key is None:
        raise RuntimeError(
            "PLATFORM_API_KEY is not set. Refusing to start an unauthenticated "
            "trading API. Add PLATFORM_API_KEY to your .env."
        )
    if settings.dry_run:
        log.warning("DRY_RUN enabled: orders are simulated, not sent (developer mode).")
        return
    if not settings.has_wallet:
        raise RuntimeError(
            "PRIVATE_KEY and FUNDER_ADDRESS must be set. See the README (Setup)."
        )
    if not settings.has_clob_creds:
        raise RuntimeError(
            "CLOB credentials missing. Run `derive-creds` to populate them in .env."
        )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PolyGate",
        version="0.1.0",
        description=(
            "A language-agnostic REST gateway to the Polymarket APIs for "
            "algorithmic and LLM trading agents. "
            "Send the `X-API-Key` header with every request."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(PlatformError)
    async def _platform_error_handler(_: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": exc.message},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "An unexpected error occurred."},
        )

    app.include_router(routes_system.router)
    app.include_router(routes_market.router)
    app.include_router(routes_market.book_router)
    app.include_router(routes_market.events_router)
    app.include_router(routes_portfolio.router)
    app.include_router(routes_trading.router)
    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: start the uvicorn server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "polygate.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
