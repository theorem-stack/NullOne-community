"""Discover Kalshi EIA gas storage bracket markets via the events API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from nullone.config import KalshiConfig, Settings, load_settings
from nullone.exchanges.kalshi.auth import auth_headers, load_private_key
from nullone.exchanges.polymarket.gamma import parse_bracket_from_title

TRADE_API_PREFIX = "/trade-api/v2"


def _sign_path(path: str) -> str:
    return path if path.startswith(TRADE_API_PREFIX) else f"{TRADE_API_PREFIX}{path}"

TRADEABLE_MARKET_STATUSES = frozenset({"open", "active"})


class DiscoveryError(Exception):
    """Raised when Kalshi market discovery fails validation."""

    def __init__(self, message: str, *, code: str = "unknown") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DiscoveredBracket:
    label: str
    min_bcf: int
    max_bcf: int
    market_ticker: str
    status: str


@dataclass(frozen=True)
class DiscoveryResult:
    event_ticker: str
    event_title: str
    week_ending: date
    brackets: list[DiscoveredBracket]


def is_tradeable_market_status(status: str | None) -> bool:
    return str(status or "").lower() in TRADEABLE_MARKET_STATUSES


def _week_patterns(week_ending: date) -> list[str]:
    month_abbr = week_ending.strftime("%b").upper()
    day = week_ending.day
    return [
        week_ending.isoformat(),
        week_ending.strftime("%Y%m%d"),
        f"{month_abbr}{day:02d}",
        f"{month_abbr}{day}",
        week_ending.strftime("%B %d, %Y"),
        week_ending.strftime("%b %d, %Y"),
        week_ending.strftime("%B %d").lower(),
        week_ending.strftime("%b %d").lower(),
    ]


def _score_event_match(event: dict[str, Any], week_ending: date) -> int:
    patterns = _week_patterns(week_ending)
    haystacks = [
        event.get("event_ticker") or "",
        event.get("title") or "",
        event.get("subtitle") or "",
    ]
    score = 0
    for haystack in haystacks:
        text = haystack.lower()
        for pattern in patterns:
            needle = pattern.lower()
            if needle in text:
                score += 10
                if haystack == event.get("event_ticker"):
                    score += 5
    ticker = (event.get("event_ticker") or "").upper()
    month_abbr = week_ending.strftime("%b").upper()
    if f"-{month_abbr}{week_ending.day:02d}" in ticker or f"-{month_abbr}{week_ending.day}" in ticker:
        score += 20
    return score


def match_event_for_week(events: list[dict[str, Any]], week_ending: date) -> dict[str, Any]:
    """Pick the best event matching a WNGSR week-ending Friday."""
    scored = [(event, _score_event_match(event, week_ending)) for event in events]
    scored = [(event, score) for event, score in scored if score > 0]
    if not scored:
        raise DiscoveryError(
            f"No Kalshi event matched week_ending={week_ending.isoformat()} "
            f"among {len(events)} candidate event(s)",
            code="event_not_found",
        )

    scored.sort(key=lambda item: item[1], reverse=True)
    best_event, best_score = scored[0]
    if len(scored) > 1 and scored[1][1] == best_score:
        candidates = [e.get("event_ticker", "?") for e, s in scored if s == best_score]
        raise DiscoveryError(
            f"Ambiguous Kalshi event match for week_ending={week_ending.isoformat()}: "
            f"{', '.join(candidates)}",
            code="event_ambiguous",
        )
    return best_event


def _parse_market_bounds(market: dict[str, Any]) -> tuple[int, int] | None:
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    if floor is not None and cap is not None:
        return int(floor), int(cap)

    for field in ("subtitle", "title", "yes_sub_title", "no_sub_title"):
        text = market.get(field) or ""
        bounds = parse_bracket_from_title(text)
        if bounds:
            return bounds

    ticker = market.get("ticker") or market.get("market_ticker") or ""
    match = re.search(r"-(\d+)$", ticker)
    if match:
        center = int(match.group(1))
        return center - 2, center + 2

    return None


def parse_brackets_from_event(event: dict[str, Any]) -> list[DiscoveredBracket]:
    """Extract bracket markets from a Kalshi event with nested markets."""
    markets = event.get("markets") or []
    brackets: list[DiscoveredBracket] = []

    for market in markets:
        status = str(market.get("status") or "unknown")
        if not is_tradeable_market_status(status):
            continue
        bounds = _parse_market_bounds(market)
        if not bounds:
            continue
        min_bcf, max_bcf = bounds
        ticker = market.get("ticker") or market.get("market_ticker") or ""
        if not ticker:
            continue
        brackets.append(
            DiscoveredBracket(
                label=f"{min_bcf}-{max_bcf}",
                min_bcf=min_bcf,
                max_bcf=max_bcf,
                market_ticker=ticker,
                status=status,
            )
        )

    return sorted(brackets, key=lambda b: b.min_bcf)


def _filter_tradeable_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for event in events:
        markets = [
            market
            for market in (event.get("markets") or [])
            if is_tradeable_market_status(market.get("status"))
        ]
        if not markets:
            continue
        filtered.append({**event, "markets": markets})
    return filtered


class KalshiDiscover:
    def __init__(
        self,
        config: KalshiConfig,
        *,
        series_ticker: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._series_ticker = series_ticker
        self._client = client
        self._owns_client = client is None
        self._private_key = load_private_key(
            path=config.private_key_path,
            pem=config.private_key_pem,
        )

    async def __aenter__(self) -> KalshiDiscover:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._config.base_url, timeout=15.0)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._client is not None
        headers = auth_headers(
            self._private_key,
            self._config.api_key,
            method="GET",
            path=_sign_path(path),
        )
        resp = await self._client.get(path, headers=headers, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def verify_series_ticker(self) -> None:
        try:
            await self._get(f"/series/{self._series_ticker}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise DiscoveryError(
                    f"Kalshi series_ticker={self._series_ticker} not found. "
                    "Set KALSHI_GAS_STORAGE_SERIES to a valid series (default: KXGAS).",
                    code="series_not_found",
                ) from exc
            raise

    async def fetch_tradeable_events(self) -> list[dict[str, Any]]:
        events_by_ticker: dict[str, dict[str, Any]] = {}
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {
                "series_ticker": self._series_ticker,
                "with_nested_markets": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._get("/events", params)
            for event in data.get("events") or []:
                ticker = event.get("event_ticker")
                if ticker:
                    events_by_ticker[ticker] = event
            cursor = data.get("cursor") or None
            if not cursor:
                break

        return _filter_tradeable_events(list(events_by_ticker.values()))

    async def discover_for_week(self, week_ending: date) -> DiscoveryResult:
        await self.verify_series_ticker()
        events = await self.fetch_tradeable_events()
        if not events:
            raise DiscoveryError(
                f"No tradeable Kalshi events found for series_ticker={self._series_ticker}. "
                "Markets may not be listed yet for this release week.",
                code="no_tradeable_events",
            )

        event = match_event_for_week(events, week_ending)
        brackets = parse_brackets_from_event(event)
        if not brackets:
            raise DiscoveryError(
                f"Event {event.get('event_ticker')} has no parseable tradeable bracket markets",
                code="no_parseable_brackets",
            )

        return DiscoveryResult(
            event_ticker=str(event.get("event_ticker") or ""),
            event_title=str(event.get("title") or ""),
            week_ending=week_ending,
            brackets=brackets,
        )


async def discover_kalshi_brackets(
    week_ending: date,
    settings: Settings | None = None,
) -> DiscoveryResult:
    settings = settings or load_settings()
    if not settings.kalshi:
        raise DiscoveryError("Kalshi credentials not configured (KALSHI_API_KEY required)", code="auth_missing")

    series = settings.kalshi_gas_storage_series.strip()
    if not series:
        raise DiscoveryError("KALSHI_GAS_STORAGE_SERIES is not set", code="series_missing")

    async with KalshiDiscover(settings.kalshi, series_ticker=series) as discover:
        return await discover.discover_for_week(week_ending)
