"""Guarded Kalshi V2 batch endpoint probe using deliberately non-crossing IOC orders."""

from __future__ import annotations

import argparse
import asyncio
import time

import orjson

from nullone.config import load_settings
from nullone.exchanges.base import OrderRequest
from nullone.exchanges.kalshi.orders import KalshiOrderClient, build_batch_order_payload
from nullone.exchanges.kalshi.ws_book import KalshiOrderBookMirror, KalshiBookVector


ACK_TEXT = "I_UNDERSTAND_THIS_CALLS_KALSHI"


def build_no_fill_probe_orders(
    vector: KalshiBookVector,
    *,
    include_bid: bool = True,
    include_ask: bool = True,
    count: float = 1.0,
    bid_price: float = 0.01,
    ask_price: float = 0.99,
) -> list[OrderRequest]:
    """Build no-cross IOC orders and fail closed if either side could fill."""
    orders: list[OrderRequest] = []
    if include_bid:
        if vector.best_ask_yes is None:
            raise ValueError("Cannot prove bid is no-fill without best_ask_yes")
        if bid_price >= vector.best_ask_yes:
            raise ValueError(
                f"Unsafe bid probe: bid_price={bid_price:.4f} crosses best_ask_yes={vector.best_ask_yes:.4f}"
            )
        orders.append(
            OrderRequest(
                market_id=vector.market_ticker,
                side="bid",
                price=bid_price,
                size_usd=bid_price * count,
                count=count,
                time_in_force="immediate_or_cancel",
            )
        )
    if include_ask:
        if vector.best_bid_yes is None:
            raise ValueError("Cannot prove ask is no-fill without best_bid_yes")
        if ask_price <= vector.best_bid_yes:
            raise ValueError(
                f"Unsafe ask probe: ask_price={ask_price:.4f} crosses best_bid_yes={vector.best_bid_yes:.4f}"
            )
        orders.append(
            OrderRequest(
                market_id=vector.market_ticker,
                side="ask",
                price=ask_price,
                size_usd=(1.0 - ask_price) * count,
                count=count,
                time_in_force="immediate_or_cancel",
            )
        )
    if not orders:
        raise ValueError("No probe orders requested")
    return orders


async def run_kalshi_batch_probe(
    *,
    ticker: str,
    acknowledge: str,
    include_bid: bool = True,
    include_ask: bool = True,
    count: float = 1.0,
    bid_price: float = 0.01,
    ask_price: float = 0.99,
) -> dict:
    timestamps: dict[str, int] = {"t0_started_ns": time.time_ns()}
    if acknowledge != ACK_TEXT:
        raise ValueError(f"Refusing to call Kalshi without --acknowledge {ACK_TEXT}")

    settings = load_settings()
    if not settings.kalshi:
        raise RuntimeError("Kalshi credentials are required")
    timestamps["t1_loaded_ns"] = time.time_ns()

    mirror = KalshiOrderBookMirror(settings.kalshi, [ticker])
    await mirror.seed_from_rest(max_price=0.99)
    timestamps["t2_book_seeded_ns"] = time.time_ns()
    vector = mirror.vector(ticker, max_price=0.99)
    orders = build_no_fill_probe_orders(
        vector,
        include_bid=include_bid,
        include_ask=include_ask,
        count=count,
        bid_price=bid_price,
        ask_price=ask_price,
    )
    timestamps["t3_safety_checked_ns"] = time.time_ns()
    payload = build_batch_order_payload(orders)
    timestamps["t4_payload_built_ns"] = time.time_ns()

    async with KalshiOrderClient(settings.kalshi) as client:
        timestamps["t5_client_ready_ns"] = time.time_ns()
        batch = await client.create_batch_orders(orders, paper=False)
        timestamps["t6_response_received_ns"] = time.time_ns()

    report = {
        "probe": "kalshi_batch_no_fill",
        "ticker": ticker,
        "will_place_orders": True,
        "intended_fill": False,
        "vector": {
            "best_bid_yes": vector.best_bid_yes,
            "best_ask_yes": vector.best_ask_yes,
            "best_bid_no": vector.best_bid_no,
            "best_ask_no": vector.best_ask_no,
        },
        "payload": payload,
        "timestamps": timestamps,
        "latency_ms": {
            "load": (timestamps["t1_loaded_ns"] - timestamps["t0_started_ns"]) / 1_000_000,
            "book_seed": (timestamps["t2_book_seeded_ns"] - timestamps["t1_loaded_ns"]) / 1_000_000,
            "safety_check": (timestamps["t3_safety_checked_ns"] - timestamps["t2_book_seeded_ns"]) / 1_000_000,
            "payload_build": (timestamps["t4_payload_built_ns"] - timestamps["t3_safety_checked_ns"]) / 1_000_000,
            "client_ready": (timestamps["t5_client_ready_ns"] - timestamps["t4_payload_built_ns"]) / 1_000_000,
            "post_round_trip": (timestamps["t6_response_received_ns"] - timestamps["t5_client_ready_ns"]) / 1_000_000,
            "total": (timestamps["t6_response_received_ns"] - timestamps["t0_started_ns"]) / 1_000_000,
        },
        "result": {
            "submitted": batch.submitted,
            "paper": batch.paper,
            "orders": [
                {
                    "fill_price": order.fill_price,
                    "fill_size": order.fill_size,
                    "order_id": order.order_id,
                    "error": order.error,
                    "remaining_count": order.remaining_count,
                    "average_fee_paid": order.average_fee_paid,
                    "ts_ms": order.ts_ms,
                }
                for order in batch.orders
            ],
        },
    }
    print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Kalshi batch endpoint with no-fill IOC orders")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--acknowledge", required=True, help=f"Must equal {ACK_TEXT}")
    parser.add_argument("--bid-only", action="store_true")
    parser.add_argument("--ask-only", action="store_true")
    parser.add_argument("--count", type=float, default=1.0)
    parser.add_argument("--bid-price", type=float, default=0.01)
    parser.add_argument("--ask-price", type=float, default=0.99)
    args = parser.parse_args()

    include_bid = not args.ask_only
    include_ask = not args.bid_only
    asyncio.run(
        run_kalshi_batch_probe(
            ticker=args.ticker,
            acknowledge=args.acknowledge,
            include_bid=include_bid,
            include_ask=include_ask,
            count=args.count,
            bid_price=args.bid_price,
            ask_price=args.ask_price,
        )
    )


if __name__ == "__main__":
    main()

