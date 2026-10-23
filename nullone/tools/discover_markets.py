"""Discover Kalshi tickers for EIA gas storage brackets."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from nullone.calendar.scheduler import next_release_after
from nullone.mapping.spec_resolver import discover_and_write_spec


async def run_discovery(
    week_ending: str | None = None,
    output: Path | None = None,
) -> Path:
    week = date.fromisoformat(week_ending) if week_ending else next_release_after().week_ending
    spec_dict, path = await discover_and_write_spec(week, output=output)
    event_ticker = spec_dict.get("kalshi_event_ticker", "?")
    bracket_count = len(spec_dict.get("brackets") or [])
    print(f"event_ticker={event_ticker} brackets={bracket_count} spec={path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Kalshi gas storage market IDs")
    parser.add_argument("--week", default=None, help="Week-ending date YYYY-MM-DD (default: next release)")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    path = asyncio.run(run_discovery(args.week, args.output))
    print(f"Wrote spec to {path}")


if __name__ == "__main__":
    main()
