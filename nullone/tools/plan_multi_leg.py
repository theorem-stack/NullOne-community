"""Plan multi-leg Kalshi execution from live books without placing orders."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import orjson

from nullone.config import load_settings
from nullone.engine.allocation import LegMarketVector, allocate_multi_leg
from nullone.exchanges.base import OrderRequest
from nullone.exchanges.kalshi.auth import auth_headers, load_private_key
from nullone.exchanges.kalshi.orders import build_batch_order_payload, trade_api_sign_path
from nullone.exchanges.kalshi.ws_book import KalshiOrderBookMirror
from nullone.mapping.scenario_index import ScenarioIndex
from nullone.mapping.spec_loader import load_spec


def _ms(timestamps: dict[str, int], start: str, end: str) -> float | None:
    if start not in timestamps or end not in timestamps:
        return None
    return (timestamps[end] - timestamps[start]) / 1_000_000


async def plan_multi_leg(
    *,
    spec_path: Path,
    net_change_bcf: int,
    wait_seconds: float = 2.0,
    output: Path | None = None,
    use_presigned_headers: bool = False,
) -> dict:
    timestamps: dict[str, int] = {"t0_started_ns": time.time_ns()}
    settings = load_settings()
    spec = load_spec(spec_path)
    if not settings.kalshi:
        raise RuntimeError("Kalshi credentials are required for live book planning")
    private_key = load_private_key(
        path=settings.kalshi.private_key_path,
        pem=settings.kalshi.private_key_pem,
    )
    timestamps["t0_key_preloaded_ns"] = time.time_ns()

    tickers = [bracket.kalshi.market_ticker for bracket in spec.brackets if bracket.kalshi]
    scenario = ScenarioIndex(spec).scenario_for_net_change(net_change_bcf)
    timestamps["t1_loaded_ns"] = time.time_ns()
    mirror = KalshiOrderBookMirror(settings.kalshi, tickers)
    await mirror.start()
    timestamps["t2_ws_started_ns"] = time.time_ns()
    try:
        await mirror.seed_from_rest(max_price=spec.execution.max_entry_price)
        timestamps["t3_rest_seed_ns"] = time.time_ns()
        cached_headers = None
        if use_presigned_headers:
            cached_headers = auth_headers(
                private_key,
                settings.kalshi.api_key,
                method="POST",
                path=trade_api_sign_path("/portfolio/events/orders/batched"),
            )
            timestamps["t3_presigned_headers_ns"] = time.time_ns()
        await asyncio.sleep(wait_seconds)
        timestamps["t4_wait_done_ns"] = time.time_ns()
        vectors: dict[str, LegMarketVector] = {}
        vector_payloads = {}
        for leg in scenario.legs:
            vector = mirror.vector(leg.market_ticker, max_price=spec.execution.max_entry_price)
            selected = LegMarketVector(
                market_ticker=vector.market_ticker,
                best_ask_yes=vector.best_ask_yes,
                best_ask_no=vector.best_ask_no,
                depth_yes_usd=vector.depth_yes_usd,
                depth_no_usd=vector.depth_no_usd,
            )
            vectors[leg.market_ticker] = selected
            vector_payloads[leg.market_ticker] = {
                "best_ask_yes": selected.best_ask_yes,
                "best_ask_no": selected.best_ask_no,
                "depth_yes_usd": selected.depth_yes_usd,
                "depth_no_usd": selected.depth_no_usd,
                "source": "seeded_mirror",
            }
        timestamps["t5_vectors_selected_ns"] = time.time_ns()

        legs = allocate_multi_leg(scenario, vectors, spec.execution)
        timestamps["t6_allocation_done_ns"] = time.time_ns()
        order_requests = [
            OrderRequest(
                market_id=leg.market_ticker,
                side=leg.v2_side,
                price=leg.limit_price_yes,
                size_usd=leg.notional_usd,
                time_in_force="immediate_or_cancel",
                count=max(1.0, leg.notional_usd / max(leg.entry_price, 0.01)),
            )
            for leg in legs
        ]
        batch_payload = build_batch_order_payload(order_requests)
        timestamps["t7_payload_built_ns"] = time.time_ns()
        batch_body = orjson.dumps(batch_payload)
        timestamps["t8_json_serialized_ns"] = time.time_ns()
        if cached_headers is not None:
            signed_headers = cached_headers
        else:
            signed_headers = auth_headers(
                private_key,
                settings.kalshi.api_key,
                method="POST",
                path=trade_api_sign_path("/portfolio/events/orders/batched"),
            )
        timestamps["t9_signed_ns"] = time.time_ns()
        report = {
            "dry_run": True,
            "will_place_orders": False,
            "spec_path": str(spec_path),
            "net_change_bcf": net_change_bcf,
            "winning_bracket": scenario.winner_label,
            "wait_seconds": wait_seconds,
            "release_capital_usd": spec.execution.release_capital_usd,
            "parse_error_rate": spec.execution.parse_error_rate,
            "timestamps": timestamps,
            "latency_ms": {
                "load": _ms(timestamps, "t0_started_ns", "t1_loaded_ns"),
                "ws_start": _ms(timestamps, "t1_loaded_ns", "t2_ws_started_ns"),
                "rest_seed": _ms(timestamps, "t2_ws_started_ns", "t3_rest_seed_ns"),
                "wait": _ms(timestamps, "t3_rest_seed_ns", "t4_wait_done_ns"),
                "vector_select": _ms(timestamps, "t4_wait_done_ns", "t5_vectors_selected_ns"),
                "allocation": _ms(timestamps, "t5_vectors_selected_ns", "t6_allocation_done_ns"),
                "payload_build": _ms(timestamps, "t6_allocation_done_ns", "t7_payload_built_ns"),
                "json_serialize": _ms(
                    timestamps, "t7_payload_built_ns", "t8_json_serialized_ns"
                ),
                "key_preload": _ms(timestamps, "t0_started_ns", "t0_key_preloaded_ns"),
                "presign_cache": _ms(
                    timestamps, "t3_rest_seed_ns", "t3_presigned_headers_ns"
                ),
                "sign": _ms(timestamps, "t8_json_serialized_ns", "t9_signed_ns"),
                "total_before_print": _ms(timestamps, "t0_started_ns", "t9_signed_ns"),
            },
            "signed_request_probe": {
                "method": "POST",
                "path": "/portfolio/events/orders/batched",
                "body_bytes": len(batch_body),
                "header_keys": sorted(signed_headers),
                "used_presigned_headers": cached_headers is not None,
                "signature_redacted": True,
                "sent": False,
            },
            "vectors": vector_payloads,
            "legs": [
                {
                    "role": leg.role,
                    "label": leg.label,
                    "ticker": leg.market_ticker,
                    "v2_side": leg.v2_side,
                    "economic_side": leg.economic_side,
                    "raw_edge": leg.raw_edge,
                    "payout_ratio": leg.payout_ratio,
                    "parse_p": leg.parse_p,
                    "parse_q": leg.parse_q,
                    "kelly_f": leg.kelly_f,
                    "weight": leg.weight,
                    "notional_usd": leg.notional_usd,
                    "limit_price_yes": leg.limit_price_yes,
                    "target_no_price": leg.target_no_price,
                    "depth_to_max_usd": leg.depth_to_max_usd,
                    "fill_prob": leg.fill_prob,
                }
                for leg in legs
            ],
            "would_send_batch": batch_payload,
        }
    finally:
        await mirror.stop()

    text = orjson.dumps(report, option=orjson.OPT_INDENT_2).decode("utf-8")
    print(text)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan multi-leg Kalshi batch without orders")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--net-change", type=int, required=True)
    parser.add_argument("--wait-seconds", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--use-presigned-headers",
        action="store_true",
        help="Use a locally cached batch auth header probe instead of signing on the hot path",
    )
    args = parser.parse_args()
    asyncio.run(
        plan_multi_leg(
            spec_path=args.spec,
            net_change_bcf=args.net_change,
            wait_seconds=args.wait_seconds,
            output=args.output,
            use_presigned_headers=args.use_presigned_headers,
        )
    )


if __name__ == "__main__":
    main()

