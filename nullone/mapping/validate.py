"""Validation for discovered and cached event specs."""

from __future__ import annotations

from nullone.exchanges.kalshi.discover import (
    DiscoveredBracket,
    DiscoveryError,
    is_tradeable_market_status,
)
from nullone.mapping.spec_loader import BracketSpec, EventSpec

MIN_BRACKET_COUNT = 3


def validate_discovered_brackets(brackets: list[DiscoveredBracket]) -> None:
    if len(brackets) < MIN_BRACKET_COUNT:
        raise DiscoveryError(
            f"Expected at least {MIN_BRACKET_COUNT} brackets, found {len(brackets)}"
        )

    ordered = sorted(brackets, key=lambda b: b.min_bcf)
    for bracket in ordered:
        if not bracket.market_ticker or bracket.market_ticker == "TBD":
            raise DiscoveryError(f"Bracket {bracket.label} has missing Kalshi ticker")
        if bracket.min_bcf > bracket.max_bcf:
            raise DiscoveryError(f"Bracket {bracket.label} has min > max")
        if not is_tradeable_market_status(bracket.status):
            raise DiscoveryError(
                f"Market {bracket.market_ticker} is not tradeable (status={bracket.status})"
            )

    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        curr = ordered[i]
        if curr.min_bcf <= prev.max_bcf:
            raise DiscoveryError(f"Bracket overlap: {prev.label} and {curr.label}")


def validate_spec_for_run(spec: EventSpec, *, week_ending: str | None = None) -> None:
    if week_ending and spec.week_ending != week_ending:
        raise DiscoveryError(
            f"Spec week_ending={spec.week_ending} does not match target {week_ending}"
        )

    kalshi_brackets = [b for b in spec.brackets if b.kalshi is not None]
    if len(kalshi_brackets) < MIN_BRACKET_COUNT:
        raise DiscoveryError(
            f"Spec has {len(kalshi_brackets)} Kalshi bracket(s); "
            f"need at least {MIN_BRACKET_COUNT}"
        )

    for bracket in spec.brackets:
        if bracket.kalshi is None:
            continue
        if not bracket.kalshi.market_ticker or bracket.kalshi.market_ticker == "TBD":
            raise DiscoveryError(f"Bracket {bracket.label} has TBD Kalshi ticker")

    _validate_bracket_bounds(spec.brackets)


def _validate_bracket_bounds(brackets: list[BracketSpec]) -> None:
    active = [b for b in brackets if b.kalshi is not None]
    if not active:
        raise DiscoveryError("Spec has no Kalshi brackets")

    ordered = sorted(active, key=lambda b: b.min_bcf)
    for bracket in ordered:
        if bracket.min_bcf > bracket.max_bcf:
            raise DiscoveryError(f"Bracket {bracket.label} has min > max")

    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        curr = ordered[i]
        if curr.min_bcf <= prev.max_bcf:
            raise DiscoveryError(f"Bracket overlap: {prev.label} and {curr.label}")
