"""Read-only Polymarket research MCP server.

Exposes public Polymarket prediction-market data - events, markets, order
books, prices, comments, holders - to MCP hosts. No account, wallet, or
credentials; every tool is read-only.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("polygate")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"
