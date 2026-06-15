"""Polymarket algorithmic-trading platform.

A language-agnostic REST gateway that exposes Polymarket market data, account
state, and trading actions so any algorithmic trader can act on Polymarket
programmatically.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("polygate")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"
