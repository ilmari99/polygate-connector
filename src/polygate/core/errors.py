"""Typed application errors that map cleanly onto HTTP responses."""

from __future__ import annotations


class PlatformError(Exception):
    """Base class for all platform errors.

    Attributes:
        message: Human-readable description (safe to return to the client).
        status_code: HTTP status code to respond with.
        code: Stable machine-readable error code.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class ConfigurationError(PlatformError):
    """The platform is missing configuration required for the request."""

    status_code = 503
    code = "configuration_error"


class UpstreamError(PlatformError):
    """A Polymarket upstream API (Gamma/CLOB/Data) returned an error."""

    status_code = 502
    code = "upstream_error"


class NotFoundError(PlatformError):
    status_code = 404
    code = "not_found"


class ValidationError(PlatformError):
    status_code = 422
    code = "validation_error"


class AuthError(PlatformError):
    status_code = 401
    code = "unauthorized"
