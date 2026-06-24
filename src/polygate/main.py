"""FastAPI application factory, error handling, and entrypoint."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from . import __version__
from .api import routes_market, routes_portfolio, routes_system, routes_trading
from .config import Settings, get_settings
from .core.env_file import find_env_path, upsert_env
from .core.errors import PlatformError
from .core.logging import configure_logging, log
from .onboarding import complete_onboarding
from .services.facade import PolymarketService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    _ensure_platform_api_key(settings)
    if settings.dry_run:
        log.warning("DRY_RUN enabled: orders are simulated, not sent (developer mode).")
    if settings.has_wallet:
        await complete_onboarding(settings)
    elif not settings.dry_run:
        log.warning(
            "No wallet configured yet; account and trading endpoints are inactive. "
            "Open http://%s:%d/setup to add your Polymarket keys.",
            settings.host,
            settings.port,
        )
    app.state.service = PolymarketService(settings)
    suffix = "  [DRY_RUN: orders simulated]" if settings.dry_run else ""
    log.info("PolyGate ready; wallet=%s%s", settings.funder_address, suffix)
    try:
        yield
    finally:
        await app.state.service.aclose()
        log.info("Platform stopped.")


def _ensure_platform_api_key(settings: Settings) -> None:
    """Generate and persist a PLATFORM_API_KEY on first run when missing.

    Onboarding only requires the two values from polymarket.com (PRIVATE_KEY and
    FUNDER_ADDRESS); the platform's own ``X-API-Key`` guard is created here and
    saved back to ``.env`` for next time, exactly like the CLOB credentials.
    """
    if settings.platform_api_key is not None:
        return
    key = secrets.token_urlsafe(32)
    path = find_env_path()
    upsert_env(path, {"PLATFORM_API_KEY": key})
    settings.platform_api_key = SecretStr(key)
    log.info("PLATFORM_API_KEY missing; generated one and saved to %s.", path)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PolyGate",
        version=__version__,
        description=(
            "A language-agnostic REST gateway to the Polymarket APIs for "
            "algorithmic and LLM trading agents. Market-data and research "
            "endpoints are public; portfolio and trading endpoints require the "
            "`X-API-Key` header."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(PlatformError)
    async def _platform_error_handler(_: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            msg = err.get("msg", "invalid value")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": "; ".join(parts) or "Invalid request.",
            },
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
    app.include_router(routes_market.research_router)
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
