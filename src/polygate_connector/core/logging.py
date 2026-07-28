"""Structured logging setup shared by the server entry points."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

log = logging.getLogger("polygate_connector")


def configure_logging(level: str = "INFO") -> None:
    """Configure root handlers once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("polygate_connector")
    root.setLevel(level.upper())
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True
