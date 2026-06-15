"""FastAPI application factory, error handling, and entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from . import __version__
from .api import routes_market, routes_portfolio, routes_system, routes_trading
from .config import Settings, get_settings
from .core.errors import PlatformError
from .core.logging import configure_logging, log
from .services.credentials import derive_clob_credentials, store_clob_credentials
from .services.facade import PolymarketService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    _startup_checks(settings)
    if not settings.dry_run:
        await _ensure_clob_credentials(settings)
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
            "PLATFORM_API_KEY is not set. Refusing to start a trading API whose "
            "account endpoints would be unprotected. Add PLATFORM_API_KEY to your .env."
        )
    if settings.dry_run:
        log.warning("DRY_RUN enabled: orders are simulated, not sent (developer mode).")
        return
    if not settings.has_wallet:
        raise RuntimeError(
            "PRIVATE_KEY and FUNDER_ADDRESS must be set. See the README (Setup)."
        )


async def _ensure_clob_credentials(settings: Settings) -> None:
    """Derive and persist CLOB API credentials on first run when missing.

    The L2 credentials are deterministically derivable from the wallet key, so the
    server obtains and persists them automatically. They are written back to
    ``.env`` for next time.
    """
    if settings.has_clob_creds:
        return
    log.info("CLOB credentials missing; deriving from wallet key ...")
    try:
        creds = await asyncio.to_thread(derive_clob_credentials, settings)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to derive CLOB credentials from the wallet key: {exc}. Check "
            "connectivity and PRIVATE_KEY, then restart the server."
        ) from exc
    path = store_clob_credentials(creds)
    settings.clob_api_key = SecretStr(creds["CLOB_API_KEY"])
    settings.clob_secret = SecretStr(creds["CLOB_SECRET"])
    settings.clob_passphrase = SecretStr(creds["CLOB_PASSPHRASE"])
    log.info("CLOB credentials derived and saved to %s.", path)


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
