"""Structured logging plus a dedicated audit log for state-changing actions.

The audit logger records every trading action (including simulated dry-run
actions) so there is always a local record of what the platform did or would
have done. Secrets are never logged.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_CONFIGURED = False

audit_logger = logging.getLogger("polygate.audit")
log = logging.getLogger("polygate")


def configure_logging(level: str = "INFO") -> None:
    """Configure root handlers once. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("polygate")
    root.setLevel(level.upper())
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def audit(action: str, *, dry_run: bool, **fields: Any) -> dict:
    """Emit a structured audit record and return it.

    Args:
        action: e.g. ``"place_order"``, ``"cancel_order"``, ``"cancel_all"``.
        dry_run: Whether the action was simulated.
        **fields: Non-secret structured context (token id, price, size, ...).
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "mode": "dry-run" if dry_run else "live",
        **fields,
    }
    audit_logger.info(json.dumps(record, default=str))
    return record
