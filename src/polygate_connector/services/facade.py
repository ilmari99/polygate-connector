"""The central service facade behind every MCP tool.

It owns the upstream HTTP client and aggregates the public Polymarket read
APIs (Gamma, CLOB, Data) behind one interface. Every operation is a
side-effect-free read of public data. List operations clamp their ``limit``
server-side and return a :class:`ListPage` with pagination metadata; detail
operations return a :class:`ResponseEnvelope`.
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from ..config import Settings
from ..constants import (
    COLLECT_SCAN_DEADLINE_SECONDS,
    DEFAULT_LIST_LIMIT,
    DEFAULT_PRICES_HISTORY_FIDELITY,
    DEFAULT_PRICES_HISTORY_INTERVAL,
    DEFAULT_TAGS_LIMIT,
    DEFAULT_WIDE_LIMIT,
    GAMMA_PAGE_LIMIT,
    MARKET_SCAN_MAX_EVENTS,
    MAX_LIST_LIMIT,
)
from ..core.errors import NotFoundError, UpstreamError, ValidationError
from ..models.common import ListPage, ResponseEnvelope
from .cache import TTLCache, ttl_for
from .http import HttpClient
from .transform import (
    Verbosity,
    clean_comments,
    clean_event,
    clean_events,
    clean_market,
    clean_markets,
    clean_search_events,
    clean_series_list,
    clean_tags,
    flatten_holders,
    summarize_order_book,
    summarize_price_history,
)


def _clamp(limit: int) -> int:
    """Bound a caller-supplied limit; the requesting model's number is untrusted."""
    return max(1, min(limit, MAX_LIST_LIMIT))


# Gamma's ``order`` sorts the named column as stored, and ``liquidity`` and
# ``volume`` are stored as *strings* - sorting them is lexicographic, so
# '99.999' ranks above '9999.9' descending. Redirect to their numeric twins.
_NUMERIC_ORDER_ALIASES = {"liquidity": "liquidityNum", "volume": "volumeNum"}


def _page(
    rows: Any, source: str, *, limit: int, offset: int, clamped: bool
) -> ListPage:
    """Wrap list rows with pagination metadata.

    A page as long as the requested ``limit`` means the upstream may have
    more, signalled by ``next_offset``; ``truncated`` is reserved for actual
    server-side reductions (here, a clamped limit).
    """
    if not isinstance(rows, list):
        return ListPage.of(rows, source)
    full_page = len(rows) >= limit
    return ListPage.of(
        rows,
        source,
        next_offset=offset + len(rows) if full_page else None,
        truncated=clamped,
    )


class PolymarketService:
    """Aggregates all upstream read access behind one interface."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = HttpClient(
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            concurrency=settings.upstream_concurrency,
        )
        self._cache = TTLCache()
        self._gamma_host = settings.gamma_host.rstrip("/")
        self._clob_host = settings.clob_host.rstrip("/")
        self._data_host = settings.data_host.rstrip("/")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _read(
        self, host: str, path: str, source: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET ``host + path`` (dropping None params) and tag the upstream ``source``.

        Successful responses are cached per (url, params) for the path's TTL;
        errors always propagate uncached.
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        ttl = ttl_for(path)
        key = (host + path, tuple(sorted((k, str(v)) for k, v in clean.items())))
        if ttl > 0:
            cached = self._cache.get(key)
            if not TTLCache.is_miss(cached):
                return cached
        data = await self._http.get_json(f"{host}{path}", params=clean, source=source)
        if ttl > 0:
            self._cache.set(key, data, ttl)
        return data

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
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
        """List markets, or fetch a single market by ``slug`` when given."""
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
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
                    "order": _NUMERIC_ORDER_ALIASES.get(order, order),
                    "ascending": ascending,
                },
                limit=limit,
                offset=offset,
            )
        rows = clean_markets(data, verbosity=verbosity)
        return _page(rows, "gamma", limit=limit, offset=offset, clamped=clamped)

    async def get_market(
        self, condition_id: str, *, verbosity: Verbosity = "compact"
    ) -> ResponseEnvelope:
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
        cleaned = clean_market(market, verbosity=verbosity)
        if verbosity == "compact" and isinstance(cleaned, dict):
            # The resolution criteria live in `description`/`resolutionSource`,
            # and a single-market fetch is exactly where they matter - the
            # detail call re-attaches them on top of the compact projection
            # (list projections still drop them).
            for key in ("description", "resolutionSource"):
                if market.get(key):
                    cleaned[key] = market[key]
        return ResponseEnvelope.of(cleaned, source="gamma")

    async def list_events(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        tag_id: int | None = None,
        series_id: int | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
        order: str | None = None,
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
        """List events; ``tag_id``/``series_id`` drill into a category or series.

        Below ``full`` verbosity each row carries a ``market_count`` and its top
        markets by liquidity instead of every nested market; ``get_event``
        returns the rest.
        """
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
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
        rows = clean_events(data, verbosity=verbosity, for_list=True)
        return _page(rows, "gamma", limit=limit, offset=offset, clamped=clamped)

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

    async def get_event(self, key: str, *, verbosity: Verbosity = "compact") -> ResponseEnvelope:
        """Fetch a single event (with its nested markets) by slug or event id."""
        event = await self._resolve_event(key)
        return ResponseEnvelope.of(clean_event(event, verbosity=verbosity), source="gamma")

    async def list_series(
        self, *, limit: int = DEFAULT_WIDE_LIMIT, offset: int = 0, verbosity: Verbosity = "minimal"
    ) -> ListPage:
        """List series - the recurring/multi-part groupings of events.

        A lightweight catalog: each series' heavy embedded ``events`` array is
        replaced with an ``event_count``. Drill into a series with
        ``list_events(series_id=...)`` or flatten it with
        ``collect_markets(series_id=...)``.
        """
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
        data = await self._read_paged(
            self._gamma_host, "/series", "gamma", {}, limit=limit, offset=offset
        )
        rows = clean_series_list(data, verbosity=verbosity)
        return _page(rows, "gamma", limit=limit, offset=offset, clamped=clamped)

    async def _scan_events(
        self,
        filters: dict[str, Any],
        *,
        active: bool | None,
        closed: bool | None,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Page an ``/events`` filter to COMPLETION for client-side flatten/narrow.

        Unlike ``_read_paged`` (which returns a prefix up to ``limit``), this never
        truncates: an incomplete scan would silently drop valid matches when we
        then filter by an attribute Gamma cannot filter for. It fails loud past
        :data:`MARKET_SCAN_MAX_EVENTS` or the wall-clock ``deadline`` instead
        (both are the caller's scope being too broad, hence validation errors),
        so the result is complete or an error - never a wrong answer.
        """
        collected: list[dict[str, Any]] = []
        cursor = 0
        while True:
            if deadline is not None and monotonic() >= deadline:
                raise ValidationError(
                    "The scope is too large to gather within the "
                    f"{COLLECT_SCAN_DEADLINE_SECONDS:.0f}s time budget; narrow it "
                    "(e.g. use a series_id, a more specific tag, or a single event)."
                )
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
                raise ValidationError(
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
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
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

        Returns the flat markets as ``rows``, each entry tagged with its parent
        ``event_id``/``event_title``/``event_slug`` so a caller reads every
        ``conditionId`` directly; ``context`` echoes the resolved scope and
        event count. The underlying series/tag scan runs to completion (never
        silently partial) and fails loud - with narrowing guidance - if the
        scope exceeds the event cap or the wall-clock deadline.
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
        deadline = monotonic() + COLLECT_SCAN_DEADLINE_SECONDS

        scope: dict[str, Any] = {}
        if scope_name == "series_id":
            events = await self._scan_events(
                {"series_id": series_id}, active=active, closed=closed, deadline=deadline
            )
            scope = {"series_id": series_id}
        elif scope_name == "tag_id":
            events = await self._scan_events(
                {"tag_id": tag_id}, active=active, closed=closed, deadline=deadline
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
                        {"series_id": sid}, active=active, closed=closed, deadline=deadline
                    ):
                        if ev.get(group_by) == match_value:
                            by_id[ev.get("id")] = ev
                events = list(by_id.values())
            scope = {"event": event, "group_by": group_by, "match_value": match_value}

        markets = _flatten_search(
            {"events": [clean_event(e, verbosity=verbosity) for e in events]}
        )["markets"]
        return ListPage.of(
            markets,
            "gamma",
            context={"scope": scope, "event_count": len(events)},
        )

    async def list_tags(
        self,
        *,
        limit: int = DEFAULT_TAGS_LIMIT,
        offset: int = 0,
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
        """Page through the category-tag catalog (a few hundred tags).

        Without an explicit limit Gamma returns an arbitrary 50 tags, which
        made the catalog impossible to enumerate; explicit paging fixes that.
        The sort is pinned to ``id`` because Gamma's default order is
        arbitrary - unstable order across offset pages could silently skip
        or duplicate rows.
        """
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
        data = await self._read_paged(
            self._gamma_host,
            "/tags",
            "gamma",
            {"order": "id", "ascending": True},
            limit=limit,
            offset=offset,
        )
        rows = clean_tags(data, verbosity=verbosity)
        return _page(rows, "gamma", limit=limit, offset=offset, clamped=clamped)

    # --- CLOB book / prices (keyed by outcome token id) ---
    async def order_book(self, token_id: str, *, full: bool = False) -> ResponseEnvelope:
        data = await self._read(self._clob_host, "/book", "clob", {"token_id": token_id})
        return ResponseEnvelope.of(summarize_order_book(data, full=full), source="clob")

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
        raw: bool = False,
    ) -> ResponseEnvelope:
        """Historical price series for an outcome token, summarized by default.

        The CLOB endpoint requires a time window: when the caller supplies neither
        an ``interval`` nor a ``start_ts``/``end_ts`` pair we default to
        :data:`DEFAULT_PRICES_HISTORY_INTERVAL` (rather than erroring on the bare
        call), pair it with :data:`DEFAULT_PRICES_HISTORY_FIDELITY` (a wide range
        needs a fidelity floor), and echo the applied ``interval`` into the
        payload so the caller knows the span it received. The point series is
        collapsed to a window summary unless ``raw`` is set.
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
        if not raw:
            data = summarize_price_history(data)
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
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
        """Full-text search over Polymarket events.

        Returns the matching events as rows; below ``full`` verbosity each row
        carries a ``market_count`` and its top markets by liquidity instead of
        every nested market. ``limit_per_type`` bounds the number of events.
        """
        clamped = (limit_per_type or 0) > MAX_LIST_LIMIT
        limit_per_type = _clamp(limit_per_type or DEFAULT_LIST_LIMIT)
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
        if not isinstance(data, dict):
            return ListPage.of(data, "gamma")
        events = data.get("events") or []
        rows = clean_search_events(events, verbosity=verbosity)
        has_more = bool((data.get("pagination") or {}).get("hasMore"))
        return ListPage.of(
            rows,
            "gamma",
            next_page=(page or 1) + 1 if has_more else None,
            truncated=clamped,
        )

    async def comments(
        self,
        event_id: int,
        *,
        limit: int = DEFAULT_WIDE_LIMIT,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
        verbosity: Verbosity = "minimal",
    ) -> ListPage:
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
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
        rows = clean_comments(data, verbosity=verbosity)
        return _page(rows, "gamma", limit=limit, offset=offset, clamped=clamped)

    async def holders(
        self, condition_id: str, *, limit: int = DEFAULT_WIDE_LIMIT, verbosity: Verbosity = "minimal"
    ) -> ListPage:
        """Top holders per outcome token, flattened to one row per holder."""
        clamped = limit > MAX_LIST_LIMIT
        limit = _clamp(limit)
        data = await self._read(
            self._data_host, "/holders", "data", {"market": condition_id, "limit": limit}
        )
        return ListPage.of(
            flatten_holders(data, verbosity=verbosity), "data", truncated=clamped
        )


def _flatten_search(data: Any) -> Any:
    """Surface a flat ``markets`` list on events grouped under a payload.

    Gamma groups markets under events, so a flat market view means walking
    ``events[].markets[]``. We add a top-level ``markets`` array (each entry
    tagged with its parent ``event_id``/``event_title``/``event_slug``) so a
    caller reads every ``conditionId`` directly without drilling into each
    event. A payload that already carries ``markets`` is returned untouched.
    Used by ``collect_markets``.
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
