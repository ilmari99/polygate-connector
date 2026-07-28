"""The central service facade behind every MCP tool.

It owns the upstream HTTP client and aggregates the public Polymarket read
APIs (Gamma, CLOB, Data) behind one interface. Every operation is a
side-effect-free read of public data.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..constants import (
    DEFAULT_PRICES_HISTORY_FIDELITY,
    DEFAULT_PRICES_HISTORY_INTERVAL,
    GAMMA_PAGE_LIMIT,
    MARKET_SCAN_MAX_EVENTS,
)
from ..core.errors import NotFoundError, UpstreamError, ValidationError
from ..models.common import ResponseEnvelope
from .http import HttpClient
from .transform import (
    clean_event,
    clean_events,
    clean_market,
    clean_markets,
    clean_search,
    clean_series_list,
    summarize_order_book,
)


class PolymarketService:
    """Aggregates all upstream read access behind one interface."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = HttpClient(
            timeout=settings.http_timeout_seconds, max_retries=settings.http_max_retries
        )
        self._gamma_host = settings.gamma_host.rstrip("/")
        self._clob_host = settings.clob_host.rstrip("/")
        self._data_host = settings.data_host.rstrip("/")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _read(
        self, host: str, path: str, source: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET ``host + path`` (dropping None params) and tag the upstream ``source``."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        return await self._http.get_json(f"{host}{path}", params=clean, source=source)

    async def _read_paged(
        self,
        host: str,
        path: str,
        source: str,
        params: dict[str, Any],
        *,
        limit: int,
        offset: int,
    ) -> Any:
        """Read a Gamma list endpoint, paging past its 100-row server cap.

        Gamma silently truncates any single page to ``GAMMA_PAGE_LIMIT`` rows, so
        a request for more than that is fanned out into consecutive offset pages
        and concatenated. Stops early when a short page signals the upstream is
        exhausted. A non-list response (an error shape) is returned untouched.
        """
        if limit <= GAMMA_PAGE_LIMIT:
            return await self._read(
                host, path, source, {**params, "limit": limit, "offset": offset}
            )
        collected: list[Any] = []
        cursor = offset
        while len(collected) < limit:
            page_size = min(GAMMA_PAGE_LIMIT, limit - len(collected))
            page = await self._read(
                host, path, source, {**params, "limit": page_size, "offset": cursor}
            )
            if not isinstance(page, list):
                return page if not collected else collected
            collected.extend(page)
            if len(page) < page_size:
                break  # Upstream returned a short page: no more rows available.
            cursor += page_size
        return collected[:limit]

    # --- Market data (Gamma) ---
    async def list_markets(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        tag_id: int | None = None,
        slug: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
        compact: bool = True,
    ) -> ResponseEnvelope:
        """List markets, or fetch a single market by ``slug`` when given."""
        if slug:
            data = await self._read(self._gamma_host, "/markets", "gamma", {"slug": slug})
        else:
            data = await self._read_paged(
                self._gamma_host,
                "/markets",
                "gamma",
                {
                    "active": active,
                    "closed": closed,
                    "tag_id": tag_id,
                    "order": order,
                    "ascending": ascending,
                },
                limit=limit,
                offset=offset,
            )
        return ResponseEnvelope.of(clean_markets(data, compact=compact), source="gamma")

    async def get_market(self, condition_id: str, *, compact: bool = True) -> ResponseEnvelope:
        """Fetch a single market by ``conditionId``, unwrapped to one object.

        Gamma's ``/markets?condition_ids=`` always returns a list; a caller asking
        for one market wants the object, not a 1-element array. We unwrap it and
        raise :class:`NotFoundError` when the id resolves to nothing, so a bad id
        fails loudly instead of returning an ambiguous empty list.
        """
        data = await self._read(
            self._gamma_host, "/markets", "gamma", {"condition_ids": condition_id}
        )
        market = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if market is None:
            raise NotFoundError(
                f"No market found for condition_id={condition_id!r}. Pass a market "
                "conditionId (0x...), e.g. from a search or list_markets result."
            )
        return ResponseEnvelope.of(clean_market(market, compact=compact), source="gamma")

    async def list_events(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        tag_id: int | None = None,
        series_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        compact: bool = True,
    ) -> ResponseEnvelope:
        """List events; ``tag_id``/``series_id`` drill into a category or series."""
        data = await self._read_paged(
            self._gamma_host,
            "/events",
            "gamma",
            {
                "active": active,
                "closed": closed,
                "tag_id": tag_id,
                "series_id": series_id,
                "order": order,
            },
            limit=limit,
            offset=offset,
        )
        return ResponseEnvelope.of(clean_events(data, compact=compact), source="gamma")

    async def _resolve_event(self, key: str) -> dict[str, Any]:
        """Resolve one event by slug or event id (a numeric ``key`` is an id).

        Raises :class:`NotFoundError` if nothing matches so a bad key fails loudly
        rather than silently returning an unrelated default list.
        """
        param = "id" if str(key).isdigit() else "slug"
        data = await self._read(self._gamma_host, "/events", "gamma", {param: key})
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        raise NotFoundError(
            f"No event found for {param}={key!r}. Pass an event slug or event id "
            "(e.g. from a search or list_events result)."
        )

    async def get_event(self, key: str, *, compact: bool = True) -> ResponseEnvelope:
        """Fetch a single event (with its nested markets) by slug or event id."""
        event = await self._resolve_event(key)
        return ResponseEnvelope.of(clean_event(event, compact=compact), source="gamma")

    async def list_series(
        self, *, limit: int = 100, offset: int = 0, compact: bool = True
    ) -> ResponseEnvelope:
        """List series - the recurring/multi-part groupings of events.

        A lightweight catalog: each series' heavy embedded ``events`` array is
        replaced with an ``event_count``. Drill in with ``list_events(series_id=...)``
        (its events) or ``collect_markets(series_id=...)`` (its flat markets).
        """
        data = await self._read_paged(
            self._gamma_host, "/series", "gamma", {}, limit=limit, offset=offset
        )
        return ResponseEnvelope.of(clean_series_list(data, compact=compact), source="gamma")

    async def _scan_events(
        self, filters: dict[str, Any], *, active: bool | None, closed: bool | None
    ) -> list[dict[str, Any]]:
        """Page an ``/events`` filter to COMPLETION for client-side flatten/narrow.

        Unlike ``_read_paged`` (which returns a prefix up to ``limit``), this never
        truncates: an incomplete scan would silently drop valid matches when we
        then filter by an attribute Gamma cannot filter for. It fails loud past
        :data:`MARKET_SCAN_MAX_EVENTS` instead, so a too-broad scope is an error,
        not a wrong answer.
        """
        collected: list[dict[str, Any]] = []
        cursor = 0
        while True:
            page = await self._read(
                self._gamma_host,
                "/events",
                "gamma",
                {**filters, "active": active, "closed": closed,
                 "limit": GAMMA_PAGE_LIMIT, "offset": cursor},
            )
            if not isinstance(page, list):
                raise UpstreamError("gamma returned a non-list response while paging /events")
            collected.extend(e for e in page if isinstance(e, dict))
            if len(page) < GAMMA_PAGE_LIMIT:
                break
            cursor += GAMMA_PAGE_LIMIT
            if cursor >= MARKET_SCAN_MAX_EVENTS:
                raise UpstreamError(
                    f"Scope spans more than {MARKET_SCAN_MAX_EVENTS} events; narrow "
                    "it (e.g. use a series_id, or a more specific tag)."
                )
        return collected

    async def collect_markets(
        self,
        *,
        series_id: int | None = None,
        tag_id: int | None = None,
        event: str | None = None,
        group_by: str | None = None,
        active: bool | None = True,
        closed: bool | None = False,
        compact: bool = True,
    ) -> ResponseEnvelope:
        """Flatten every atomic market under one grouping node into a flat list.

        Exactly one scope is required:

        * ``series_id`` - every market in that series' events.
        * ``tag_id`` - every market in that category's events.
        * ``event`` (slug or id) - a single event's markets. With ``group_by`` set
          to an event attribute (e.g. ``"gameId"``), the event is first expanded
          to its whole series and narrowed to sibling events sharing the anchor's
          value for that attribute - the only way to gather e.g. a sports
          fixture's sub-markets, since Gamma has no server-side filter for such
          per-event attributes (no key is special-cased; you name the attribute).

        Returns a flat ``markets`` list, each entry tagged with its parent
        ``event_id``/``event_title``/``event_slug`` so a caller reads every
        ``conditionId`` and ``clobTokenIds`` directly. The underlying series/tag
        scan runs to completion (never truncated) and fails loud if the scope is
        too broad, so the result is complete or an error - never silently partial.
        """
        chosen = [(n, v) for n, v in
                  (("series_id", series_id), ("tag_id", tag_id), ("event", event))
                  if v is not None]
        if len(chosen) != 1:
            raise ValidationError(
                "collect_markets requires exactly one of series_id, tag_id, or event."
            )
        scope_name, _ = chosen[0]
        if group_by is not None and scope_name != "event":
            raise ValidationError("group_by is only valid together with 'event'.")

        scope: dict[str, Any] = {}
        if scope_name == "series_id":
            events = await self._scan_events(
                {"series_id": series_id}, active=active, closed=closed
            )
            scope = {"series_id": series_id}
        elif scope_name == "tag_id":
            events = await self._scan_events(
                {"tag_id": tag_id}, active=active, closed=closed
            )
            scope = {"tag_id": tag_id}
        else:
            anchor = await self._resolve_event(event)  # type: ignore[arg-type]
            match_value = anchor.get(group_by) if group_by else None
            series_ids = [
                s["id"]
                for s in (anchor.get("series") or [])
                if isinstance(s, dict) and s.get("id") is not None
            ]
            if group_by is None or match_value is None or not series_ids:
                events = [anchor]
            else:
                by_id: dict[Any, dict[str, Any]] = {anchor.get("id"): anchor}
                for sid in series_ids:
                    for ev in await self._scan_events(
                        {"series_id": sid}, active=active, closed=closed
                    ):
                        if ev.get(group_by) == match_value:
                            by_id[ev.get("id")] = ev
                events = list(by_id.values())
            scope = {"event": event, "group_by": group_by, "match_value": match_value}

        payload = {
            "scope": scope,
            "event_count": len(events),
            "markets": _flatten_search(
                {"events": [clean_event(e, compact=compact) for e in events]}
            )["markets"],
        }
        payload["market_count"] = len(payload["markets"])
        return ResponseEnvelope.of(payload, source="gamma")

    async def list_tags(self) -> ResponseEnvelope:
        data = await self._read(self._gamma_host, "/tags", "gamma")
        return ResponseEnvelope.of(data, source="gamma")

    # --- CLOB book / prices (keyed by outcome token id) ---
    async def order_book(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(self._clob_host, "/book", "clob", {"token_id": token_id})
        return ResponseEnvelope.of(summarize_order_book(data), source="clob")

    async def last_trade_price(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(
            self._clob_host, "/last-trade-price", "clob", {"token_id": token_id}
        )
        return ResponseEnvelope.of(data, source="clob")

    async def prices_history(
        self,
        token_id: str,
        *,
        interval: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> ResponseEnvelope:
        """Historical price series for an outcome token.

        The CLOB endpoint requires a time window: when the caller supplies neither
        an ``interval`` nor a ``start_ts``/``end_ts`` pair we default to
        :data:`DEFAULT_PRICES_HISTORY_INTERVAL` (rather than erroring on the bare
        call), pair it with :data:`DEFAULT_PRICES_HISTORY_FIDELITY` (a wide range
        needs a fidelity floor), and echo the applied ``interval`` into the
        payload so the caller knows the span it received.
        """
        if interval is None and start_ts is None and end_ts is None:
            interval = DEFAULT_PRICES_HISTORY_INTERVAL
            if fidelity is None:
                fidelity = DEFAULT_PRICES_HISTORY_FIDELITY
        data = await self._read(
            self._clob_host,
            "/prices-history",
            "clob",
            {
                "market": token_id,
                "interval": interval,
                "startTs": start_ts,
                "endTs": end_ts,
                "fidelity": fidelity,
            },
        )
        if interval is not None and isinstance(data, dict):
            data = {**data, "interval": interval}
        return ResponseEnvelope.of(data, source="clob")

    # --- Research ---
    async def search(
        self,
        q: str,
        *,
        limit_per_type: int | None = None,
        page: int | None = None,
        events_status: str | None = None,
        compact: bool = True,
        flatten: bool = False,
    ) -> ResponseEnvelope:
        """Full-text search; optionally surface a flat ``markets`` list.

        Results group under ``events`` whose nested markets already carry decoded
        ``clobTokenIds``. The raw payload has no top-level ``markets`` array; when
        ``flatten`` is set we synthesize one (see ``_flatten_search``), each entry
        tagged with its parent ``event_id``/``event_title``/``event_slug``. It is
        off by default because it duplicates every nested market and roughly
        doubles an already-large payload.
        """
        data = await self._read(
            self._gamma_host,
            "/public-search",
            "gamma",
            {
                "q": q,
                "limit_per_type": limit_per_type,
                "page": page,
                "events_status": events_status,
            },
        )
        if flatten:
            data = _flatten_search(data)
        cleaned = clean_search(data, compact=compact)
        return ResponseEnvelope.of(cleaned, source="gamma")

    async def comments(
        self,
        event_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> ResponseEnvelope:
        data = await self._read(
            self._gamma_host,
            "/comments",
            "gamma",
            {
                "parent_entity_type": "Event",
                "parent_entity_id": event_id,
                "limit": limit,
                "offset": offset,
                "order": order,
                "ascending": ascending,
            },
        )
        return ResponseEnvelope.of(data, source="gamma")

    async def holders(self, condition_id: str, *, limit: int = 100) -> ResponseEnvelope:
        data = await self._read(
            self._data_host, "/holders", "data", {"market": condition_id, "limit": limit}
        )
        return ResponseEnvelope.of(data, source="data")



def _flatten_search(data: Any) -> Any:
    """Surface a flat ``markets`` list on events grouped under a payload.

    Gamma groups markets under events, so the outcome token ids live at
    ``events[].markets[].clobTokenIds``. We add a top-level ``markets`` array
    (each entry tagged with its parent ``event_id``/``event_title``/``event_slug``)
    so a caller reads ``clobTokenIds`` directly without drilling into every event.
    A response that already carries ``markets`` is returned untouched. Shared by
    ``search`` and ``collect_markets`` (which passes ``{"events": [...]}``).
    """
    if not isinstance(data, dict) or "markets" in data:
        return data
    flat: list[dict] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        event_title = event.get("title")
        event_slug = event.get("slug")
        for market in event.get("markets") or []:
            if isinstance(market, dict):
                flat.append(
                    {
                        **market,
                        "event_id": event_id,
                        "event_title": event_title,
                        "event_slug": event_slug,
                    }
                )
    return {**data, "markets": flat}
