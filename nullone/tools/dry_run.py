"""Validate exchange connectivity and book snapshots without waiting for EIA release."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
import orjson

from nullone.config import load_settings
from nullone.exchanges.kalshi.ws_book import KalshiOrderBookMirror
from nullone.exchanges.polymarket.book import PolymarketBookClient
from nullone.mapping.spec_resolver import resolve_spec


async def dry_run(
    *,
    spec_path: Path | str | None = None,
    week_ending: str | None = None,
    no_discover: bool = False,
    force_discover: bool = False,
) -> dict:
    from datetime import date

    week = date.fromisoformat(week_ending) if week_ending else None
    spec, path = await resolve_spec(
        week_ending=week,
        spec_path=Path(spec_path) if spec_path else None,
        no_discover=no_discover,
        force_discover=force_discover,
    )
    settings = load_settings()
    report: dict = {"event_id": spec.event_id, "spec_path": str(path), "kalshi": {}, "polymarket": {}}

    kalshi_tickers = [b.kalshi.market_ticker for b in spec.brackets if b.kalshi]
    if settings.kalshi and kalshi_tickers:
        mirror = KalshiOrderBookMirror(settings.kalshi, kalshi_tickers)
        await mirror.start()
        await asyncio.sleep(2)
        for ticker in kalshi_tickers:
            snap = mirror.snapshot(ticker)
            report["kalshi"][ticker] = {
                "best_ask": snap.best_ask,
                "best_bid": snap.best_bid,
            }
        await mirror.stop()

    async with httpx.AsyncClient() as http:
        if settings.polymarket:
            book_client = PolymarketBookClient(settings.polymarket, http)
            for bracket in spec.brackets:
                if not bracket.polymarket:
                    continue
                snap = await book_client.get_book(bracket.polymarket.token_id)
                report["polymarket"][bracket.label] = {
                    "token_id": bracket.polymarket.token_id,
                    "best_ask": snap.best_ask,
                    "best_bid": snap.best_bid,
                }

    print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run book connectivity")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--week", default=None)
    parser.add_argument("--no-discover", action="store_true")
    parser.add_argument("--force-discover", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        dry_run(
            spec_path=args.spec,
            week_ending=args.week,
            no_discover=args.no_discover,
            force_discover=args.force_discover,
        )
    )


if __name__ == "__main__":
    main()
