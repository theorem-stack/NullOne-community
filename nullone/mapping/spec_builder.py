"""Build event spec YAML dicts from discovery results."""

from __future__ import annotations

from datetime import date
from typing import Any

from nullone.calendar.scheduler import release_for_week_ending
from nullone.exchanges.kalshi.discover import DiscoveredBracket, DiscoveryResult
from nullone.config import load_settings


def brackets_to_dict(brackets: list[DiscoveredBracket]) -> list[dict[str, Any]]:
    return [
        {
            "label": bracket.label,
            "min": bracket.min_bcf,
            "max": bracket.max_bcf,
            "kalshi": {"market_ticker": bracket.market_ticker},
        }
        for bracket in sorted(brackets, key=lambda b: b.min_bcf)
    ]


def build_event_spec_dict(
    week_ending: date,
    brackets: list[DiscoveredBracket],
    *,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    release_window = release_for_week_ending(week_ending)
    exec_defaults = {
        "min_edge_after_fees": 0.05,
        "max_entry_price": 0.92,
        "release_capital_usd": settings.release_capital_usd,
        "execution_strategy": settings.execution_strategy,
        "mode": settings.mode,
    }
    if execution:
        exec_defaults.update(execution)
    exec_defaults["release_capital_usd"] = settings.release_capital_usd
    exec_defaults["execution_strategy"] = settings.execution_strategy
    exec_defaults["mode"] = settings.mode

    return {
        "event_id": f"eia_wngsr_{week_ending.isoformat()}",
        "week_ending": week_ending.isoformat(),
        "release_at_et": release_window.release_at.isoformat(),
        "metric": "net_change_bcf",
        "region": "lower48",
        "brackets": brackets_to_dict(brackets),
        "execution": exec_defaults,
    }


def build_event_spec_dict_from_discovery(
    result: DiscoveryResult,
    *,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = build_event_spec_dict(result.week_ending, result.brackets, execution=execution)
    spec["kalshi_event_ticker"] = result.event_ticker
    return spec
