from __future__ import annotations

import asyncio
import gc
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from nullone.calendar.scheduler import ET, release_for_week_ending, seconds_until_release
from nullone.config import Settings, load_settings
from nullone.engine.allocation import LegMarketVector, allocate_multi_leg
from nullone.engine.risk import evaluate_trade, order_price_for_bracket
from nullone.exchanges.base import OrderRequest, OrderResult
from nullone.exchanges.kalshi.orders import KalshiOrderClient
from nullone.exchanges.kalshi.ws_book import KalshiOrderBookMirror
from nullone.exchanges.polymarket.book import PolymarketBookClient
from nullone.exchanges.polymarket.orders import PolymarketOrderClient
from nullone.mapping.bracket import BracketMapper
from nullone.mapping.scenario_index import ScenarioIndex
from nullone.mapping.spec_loader import EventSpec, load_spec
from nullone.sources.eia_wngsr import WNGSRPoller
from nullone.telemetry.logger import ReleaseLogger
from nullone.telemetry.metrics import ReleaseMetrics, StageTimer

ET_ZONE = ZoneInfo("America/New_York")
TraceEvent = Callable[[dict[str, Any]], None]


def _trace(trace_event: TraceEvent | None, event: str, **fields: Any) -> None:
    if trace_event is None:
        return
    payload = {
        "event": event,
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **fields,
    }
    trace_event(payload)


def _simulated_book():
    from nullone.exchanges.base import OrderBookLevel, OrderBookSnapshot

    return OrderBookSnapshot(
        best_ask=0.85,
        best_bid=0.40,
        asks=[OrderBookLevel(price=0.85, size=100)],
        bids=[OrderBookLevel(price=0.40, size=100)],
    )


@asynccontextmanager
async def _http_client(enabled: bool):
    if not enabled:
        yield None
        return
    async with httpx.AsyncClient(follow_redirects=True) as client:
        yield client


def _paper_order_result(price: float, size_usd: float) -> OrderResult:
    size = max(1.0, size_usd / max(price, 0.01))
    return OrderResult(
        submitted=False,
        fill_price=price,
        fill_size=size,
        order_id=None,
        paper=True,
    )


def _simulated_vectors(spec: EventSpec) -> dict[str, LegMarketVector]:
    vectors = {}
    for bracket in spec.brackets:
        if bracket.kalshi is None:
            continue
        vectors[bracket.kalshi.market_ticker] = LegMarketVector(
            market_ticker=bracket.kalshi.market_ticker,
            best_ask_yes=0.85,
            best_ask_no=0.60,
            depth_yes_usd=85.0,
            depth_no_usd=60.0,
        )
    return vectors


def _auto_multi_leg_supported(spec: EventSpec) -> bool:
    kalshi_brackets = [bracket for bracket in spec.brackets if bracket.kalshi is not None]
    return len(kalshi_brackets) >= 2


class ReleasePipeline:
    def __init__(
        self,
        spec: EventSpec,
        settings: Settings | None = None,
        *,
        mode_override: str | None = None,
        trace_event: TraceEvent | None = None,
    ) -> None:
        self.spec = spec
        if settings is None:
            _trace(trace_event, "settings_load_start")
            settings_started = time.perf_counter()
            self.settings = load_settings()
            _trace(
                trace_event,
                "settings_loaded",
                duration_ms=round((time.perf_counter() - settings_started) * 1000, 3),
            )
        else:
            self.settings = settings
        self.mode = (mode_override or spec.execution.mode or self.settings.mode).lower()
        self.mapper = BracketMapper(spec)
        self.scenario_index = ScenarioIndex(spec)
        self.logger = ReleaseLogger(self.settings.log_dir)

    async def run(
        self,
        *,
        simulate_fixture: Path | None = None,
        trace_event: TraceEvent | None = None,
        profiler_blackout_at: datetime | None = None,
    ) -> dict:
        timer = StageTimer()
        paper = self.mode != "live"
        execute_started = time.perf_counter()
        _trace(trace_event, "execute_start")
        gc_disabled_for_blackout = False

        def _disable_gc_for_blackout() -> None:
            nonlocal gc_disabled_for_blackout
            if gc_disabled_for_blackout or not gc.isenabled():
                return
            gc.disable()
            gc_disabled_for_blackout = True
            _trace(trace_event, "gc_disabled", reason="profiler_blackout")

        week_ending = datetime.strptime(self.spec.week_ending, "%Y-%m-%d").date()
        if self.spec.release_at_et:
            release_at = datetime.fromisoformat(self.spec.release_at_et).replace(tzinfo=ET)
        else:
            release_at = release_for_week_ending(week_ending).release_at

        kalshi_tickers = [
            b.kalshi.market_ticker for b in self.spec.brackets if b.kalshi is not None
        ]

        try:
            async with _http_client(simulate_fixture is None) as http:
                kalshi_book: KalshiOrderBookMirror | None = None
                if self.settings.kalshi and kalshi_tickers and simulate_fixture is None:
                    kalshi_book = KalshiOrderBookMirror(self.settings.kalshi, kalshi_tickers)
                    await kalshi_book.start()
                    await kalshi_book.seed_from_rest(max_price=self.spec.execution.max_entry_price)

                poly_book_client: PolymarketBookClient | None = None
                if self.settings.polymarket and simulate_fixture is None:
                    poly_book_client = PolymarketBookClient(self.settings.polymarket, http)

                kalshi_orders: KalshiOrderClient | None = None
                if self.settings.kalshi and simulate_fixture is None:
                    kalshi_orders = KalshiOrderClient(self.settings.kalshi, http)
                    await kalshi_orders.__aenter__()

                poly_orders: PolymarketOrderClient | None = None
                if self.settings.polymarket and simulate_fixture is None:
                    poly_orders = PolymarketOrderClient(self.settings.polymarket)

                if simulate_fixture is not None:
                    from nullone.sources.parsers import parse_wngsr_json

                    _trace(trace_event, "source_fetch_start", source="fixture")
                    t0 = time.perf_counter()
                    raw = simulate_fixture.read_bytes()
                    fetch_ms = (time.perf_counter() - t0) * 1000
                    _trace(trace_event, "source_fetch_done", duration_ms=round(fetch_ms, 3))
                    t1 = time.perf_counter()
                    reading = parse_wngsr_json(raw)
                    parse_ms = (time.perf_counter() - t1) * 1000
                    poll_result = type(
                        "PollResult",
                        (),
                        {"reading": reading, "fetch_ms": fetch_ms, "parse_ms": parse_ms, "source": "fixture"},
                    )()
                else:
                    baseline_net_change: int | None = None
                    async with WNGSRPoller(http) as poller:
                        baseline = await poller.fetch_once()
                        baseline_net_change = baseline.reading.net_change_bcf

                        wait_s = seconds_until_release(release_at)
                        _trace(trace_event, "release_wait_start", wait_seconds=round(max(wait_s, 0), 3))
                        if wait_s > 0:
                            wait_until_pre_poll_s = max(0.0, wait_s - 15)
                            if profiler_blackout_at is not None:
                                now_utc = datetime.now(UTC)
                                if profiler_blackout_at <= now_utc:
                                    _disable_gc_for_blackout()
                                else:
                                    blackout_wait_s = (profiler_blackout_at - now_utc).total_seconds()
                                    if 0 < blackout_wait_s < wait_until_pre_poll_s:
                                        await asyncio.sleep(blackout_wait_s)
                                        _disable_gc_for_blackout()
                                        await asyncio.sleep(wait_until_pre_poll_s - blackout_wait_s)
                                    else:
                                        await asyncio.sleep(wait_until_pre_poll_s)
                                        if profiler_blackout_at <= datetime.now(UTC):
                                            _disable_gc_for_blackout()
                            else:
                                await asyncio.sleep(wait_until_pre_poll_s)
                        _trace(trace_event, "release_wait_done")

                        _trace(trace_event, "live_source_fetch_start")
                        _trace(trace_event, "source_fetch_start", source="live")
                        with timer.stage("fetch"):
                            poll_result = await poller.wait_for_release(
                                expected_week=self.spec.week_ending,
                                baseline_net_change=baseline_net_change,
                            )
                        _trace(
                            trace_event,
                            "live_source_fetch_done",
                            duration_ms=round(poll_result.fetch_ms, 3),
                        )
                        _trace(
                            trace_event,
                            "source_fetch_done",
                            duration_ms=round(poll_result.fetch_ms, 3),
                        )

                with timer.stage("parse"):
                    reading = poll_result.reading
                    if reading.revision_flag:
                        raise RuntimeError("Revision flag set — aborting")
                _trace(
                    trace_event,
                    "parse_done",
                    duration_ms=round(poll_result.parse_ms + timer.durations_ms.get("parse", 0), 3),
                )

                with timer.stage("map"):
                    winning = self.mapper.map_net_change(reading.net_change_bcf)
                _trace(
                    trace_event,
                    "map_done",
                    duration_ms=round(timer.durations_ms.get("map", 0), 3),
                )

                execution_tasks = []
                kalshi_result = None
                poly_result = None
                multi_leg_result = None
                scheduled_multi_leg = False
                timestamps: dict[str, int] = {}

                attempt_multi_leg = (
                    self.spec.execution.execution_strategy == "auto"
                    and winning.kalshi is not None
                    and _auto_multi_leg_supported(self.spec)
                    and (kalshi_orders or simulate_fixture is not None)
                )
                if attempt_multi_leg:
                    timestamps["t0_eia_parsed_ns"] = time.time_ns()
                    scenario = self.scenario_index.scenario_for_net_change(reading.net_change_bcf)
                    vectors: dict[str, LegMarketVector] = {}
                    if kalshi_book:
                        for leg in scenario.legs:
                            vector = kalshi_book.vector(
                                leg.market_ticker,
                                max_price=self.spec.execution.max_entry_price,
                            )
                            vectors[leg.market_ticker] = LegMarketVector(
                                market_ticker=vector.market_ticker,
                                best_ask_yes=vector.best_ask_yes,
                                best_ask_no=vector.best_ask_no,
                                depth_yes_usd=vector.depth_yes_usd,
                                depth_no_usd=vector.depth_no_usd,
                            )
                    else:
                        vectors = _simulated_vectors(self.spec)
                    timestamps["t1_vector_read_ns"] = time.time_ns()
                    _trace(
                        trace_event,
                        "vector_read_done",
                        duration_ms=round(
                            (timestamps["t1_vector_read_ns"] - timestamps["t0_eia_parsed_ns"])
                            / 1_000_000,
                            3,
                        ),
                    )

                    legs = allocate_multi_leg(scenario, vectors, self.spec.execution)
                    timestamps["t2_kelly_done_ns"] = time.time_ns()
                    _trace(
                        trace_event,
                        "kelly_done",
                        duration_ms=round(
                            (timestamps["t2_kelly_done_ns"] - timestamps["t1_vector_read_ns"])
                            / 1_000_000,
                            3,
                        ),
                    )
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
                    timestamps["t3_json_built_ns"] = time.time_ns()
                    _trace(
                        trace_event,
                        "order_build_done",
                        duration_ms=round(
                            (timestamps["t3_json_built_ns"] - timestamps["t2_kelly_done_ns"])
                            / 1_000_000,
                            3,
                        ),
                    )

                    async def _kalshi_batch() -> dict:
                        with timer.stage("order"):
                            batch_id = str(uuid.uuid4())
                            if kalshi_orders:
                                timestamps["t4_crypto_signed_ns"] = time.time_ns()
                                batch = await kalshi_orders.create_batch_orders(
                                    order_requests,
                                    paper=paper,
                                )
                                timestamps["t5_post_sent_ns"] = time.time_ns()
                            else:
                                timestamps["t4_crypto_signed_ns"] = time.time_ns()
                                batch = None
                                timestamps["t5_post_sent_ns"] = time.time_ns()
                            return {
                                "batch_id": batch_id,
                                "submitted": bool(batch.submitted) if batch else False,
                                "paper": bool(batch.paper) if batch else True,
                                "legs": [
                                    {
                                        "role": leg.role,
                                        "label": leg.label,
                                        "ticker": leg.market_ticker,
                                        "v2_side": leg.v2_side,
                                        "economic_side": leg.economic_side,
                                        "raw_edge": round(leg.raw_edge, 6),
                                        "payout_ratio": round(leg.payout_ratio, 6),
                                        "parse_p": round(leg.parse_p, 6),
                                        "parse_q": round(leg.parse_q, 6),
                                        "kelly_f": round(leg.kelly_f, 6),
                                        "weight": round(leg.weight, 6),
                                        "notional_usd": round(leg.notional_usd, 4),
                                        "limit_price_yes": round(leg.limit_price_yes, 4),
                                        "target_no_price": (
                                            round(leg.target_no_price, 4)
                                            if leg.target_no_price is not None
                                            else None
                                        ),
                                        "max_slippage_yes": self.spec.execution.max_slippage_yes,
                                        "depth_to_max_usd": round(leg.depth_to_max_usd, 4),
                                        "fill_prob": round(leg.fill_prob, 6),
                                        "submitted": (
                                            bool(batch.orders[idx].submitted or batch.orders[idx].paper)
                                            if batch and idx < len(batch.orders)
                                            else True
                                        ),
                                        "fill_price": (
                                            batch.orders[idx].fill_price
                                            if batch and idx < len(batch.orders)
                                            else leg.limit_price_yes
                                        ),
                                        "fill_size": (
                                            batch.orders[idx].fill_size
                                            if batch and idx < len(batch.orders)
                                            else max(1.0, leg.notional_usd / max(leg.limit_price_yes, 0.01))
                                        ),
                                        "order_id": (
                                            batch.orders[idx].order_id
                                            if batch and idx < len(batch.orders)
                                            else None
                                        ),
                                        "paper": bool(batch.paper) if batch else True,
                                    }
                                    for idx, leg in enumerate(legs)
                                ],
                            }

                    if order_requests:
                        execution_tasks.append(("kalshi_multi_leg", _kalshi_batch()))
                        scheduled_multi_leg = True

                if (
                    not scheduled_multi_leg
                    and winning.kalshi
                    and (kalshi_orders or simulate_fixture is not None)
                ):
                    book = (
                        kalshi_book.snapshot(winning.kalshi.market_ticker)
                        if kalshi_book
                        else _simulated_book()
                    )
                    decision = evaluate_trade(self.spec.execution, book, taker_fee_rate=0.01)
                    if decision.approved:
                        price = order_price_for_bracket(decision, winning)

                        async def _kalshi() -> OrderResult:
                            with timer.stage("order"):
                                if kalshi_orders:
                                    return await kalshi_orders.create_order(
                                        OrderRequest(
                                            market_id=winning.kalshi.market_ticker,
                                            side="bid",
                                            price=price,
                                            size_usd=self.spec.execution.release_capital_usd,
                                            time_in_force="immediate_or_cancel",
                                        ),
                                        paper=paper,
                                    )
                                return _paper_order_result(
                                    price, self.spec.execution.release_capital_usd
                                )

                        execution_tasks.append(("kalshi", _kalshi()))
                    else:
                        kalshi_result = {
                            "submitted": False,
                            "reason": decision.reason,
                            "fill_price": None,
                            "fill_size": 0,
                        }

                if winning.polymarket and (poly_orders or simulate_fixture is not None):
                    book = (
                        await poly_book_client.get_book(winning.polymarket.token_id)
                        if poly_book_client
                        else _simulated_book()
                    )
                    fee = (
                        await poly_orders.get_fee_rate_bps(winning.polymarket.token_id)
                        if poly_orders
                        else 0.02
                    )
                    decision = evaluate_trade(self.spec.execution, book, taker_fee_rate=fee)
                    if decision.approved:
                        price = order_price_for_bracket(decision, winning)

                        async def _poly() -> OrderResult:
                            with timer.stage("order"):
                                if poly_orders and winning.polymarket:
                                    return await poly_orders.create_order(
                                        OrderRequest(
                                            market_id=winning.polymarket.token_id,
                                            side="buy",
                                            price=price,
                                            size_usd=self.spec.execution.release_capital_usd,
                                            time_in_force="fill_or_kill",
                                        ),
                                        winning.polymarket,
                                        paper=paper,
                                    )
                                return _paper_order_result(
                                    price, self.spec.execution.release_capital_usd
                                )

                        execution_tasks.append(("polymarket", _poly()))
                    else:
                        poly_result = {
                            "submitted": False,
                            "reason": decision.reason,
                            "fill_price": None,
                            "fill_size": 0,
                        }

                if execution_tasks:
                    results = await asyncio.gather(*(task for _, task in execution_tasks))
                    for (name, _), result in zip(execution_tasks, results):
                        if name == "kalshi_multi_leg":
                            multi_leg_result = result
                            kalshi_result = {
                                "submitted": result["submitted"] or result["paper"],
                                "paper": result["paper"],
                                "batch_id": result["batch_id"],
                                "leg_count": len(result["legs"]),
                            }
                            continue

                        payload = {
                            "submitted": result.submitted or result.paper,
                            "fill_price": result.fill_price,
                            "fill_size": result.fill_size,
                            "order_id": result.order_id,
                            "paper": result.paper,
                        }
                        if name == "kalshi":
                            kalshi_result = payload
                        else:
                            poly_result = payload

                _trace(
                    trace_event,
                    "order_submit_done",
                    duration_ms=round(timer.durations_ms.get("order", 0), 3),
                    paper=paper,
                )

                if kalshi_book:
                    await kalshi_book.stop()
                if kalshi_orders:
                    await kalshi_orders.__aexit__(None, None, None)
        finally:
            if gc_disabled_for_blackout:
                gc.enable()
                _trace(trace_event, "gc_enabled", reason="post_execute")

        metrics = ReleaseMetrics(
            event_id=self.spec.event_id,
            t_fetch_ms=poll_result.fetch_ms,
            t_parse_ms=poll_result.parse_ms + timer.durations_ms.get("parse", 0),
            t_map_ms=timer.durations_ms.get("map", 0),
            t_order_ms=timer.durations_ms.get("order", 0),
            net_change_bcf=reading.net_change_bcf,
            winning_bracket=winning.label,
            kalshi=kalshi_result,
            polymarket=poly_result,
            mode=self.mode,
        )

        record = metrics.to_dict()
        record["execution_strategy"] = self.spec.execution.execution_strategy
        record["release_capital_usd"] = self.spec.execution.release_capital_usd
        if multi_leg_result is not None:
            record["multi_leg"] = True
            record["parse_error_rate"] = self.spec.execution.parse_error_rate
            record["timestamps"] = timestamps
            record["kalshi_multi_leg"] = multi_leg_result
            record["t_read_vectors_ms"] = (
                (timestamps["t1_vector_read_ns"] - timestamps["t0_eia_parsed_ns"]) / 1_000_000
                if "t1_vector_read_ns" in timestamps and "t0_eia_parsed_ns" in timestamps
                else None
            )
            record["t_kelly_ms"] = (
                (timestamps["t2_kelly_done_ns"] - timestamps["t1_vector_read_ns"]) / 1_000_000
                if "t2_kelly_done_ns" in timestamps and "t1_vector_read_ns" in timestamps
                else None
            )
            record["t_batch_ms"] = timer.durations_ms.get("order", 0)
        else:
            record["multi_leg"] = False
        self.logger.write(record)
        _trace(
            trace_event,
            "execute_done",
            duration_ms=round((time.perf_counter() - execute_started) * 1000, 3),
        )
        return record


async def run_from_spec_path(
    spec_path: Path | str,
    *,
    mode: str | None = None,
    simulate_fixture: Path | None = None,
    trace_event: TraceEvent | None = None,
) -> dict:
    spec = load_spec(spec_path)
    _trace(trace_event, "pipeline_init_start")
    pipeline_started = time.perf_counter()
    pipeline = ReleasePipeline(spec, mode_override=mode, trace_event=trace_event)
    _trace(
        trace_event,
        "pipeline_initialized",
        duration_ms=round((time.perf_counter() - pipeline_started) * 1000, 3),
    )
    return await pipeline.run(
        simulate_fixture=simulate_fixture,
        trace_event=trace_event,
    )


async def run_from_spec(
    spec: EventSpec,
    *,
    mode: str | None = None,
    simulate_fixture: Path | None = None,
    trace_event: TraceEvent | None = None,
    profiler_blackout_at: datetime | None = None,
) -> dict:
    _trace(trace_event, "pipeline_init_start")
    pipeline_started = time.perf_counter()
    pipeline = ReleasePipeline(spec, mode_override=mode, trace_event=trace_event)
    _trace(
        trace_event,
        "pipeline_initialized",
        duration_ms=round((time.perf_counter() - pipeline_started) * 1000, 3),
    )
    return await pipeline.run(
        simulate_fixture=simulate_fixture,
        trace_event=trace_event,
        profiler_blackout_at=profiler_blackout_at,
    )


async def run_from_runtime_config_path(
    config_path: Path | str,
    *,
    mode: str | None = None,
    simulate_fixture: Path | None = None,
    trace_event: TraceEvent | None = None,
) -> dict:
    from nullone.runtime_config import load_runtime_spec

    _trace(trace_event, "runtime_config_load_start")
    runtime_config_started = time.perf_counter()
    runtime_config, spec = load_runtime_spec(config_path)
    _trace(
        trace_event,
        "runtime_config_loaded",
        duration_ms=round((time.perf_counter() - runtime_config_started) * 1000, 3),
    )
    blackout_at_raw = runtime_config.runtime.get("profiler_blackout_at")
    blackout_at = (
        datetime.fromisoformat(str(blackout_at_raw).replace("Z", "+00:00"))
        if blackout_at_raw
        else None
    )
    result = await run_from_spec(
        spec,
        mode=mode,
        simulate_fixture=simulate_fixture,
        trace_event=trace_event,
        profiler_blackout_at=blackout_at,
    )
    result["runtime_config_path"] = str(config_path)
    result["run_id"] = runtime_config.runtime.get("run_id")
    return result


async def run_release(
    *,
    week_ending: str | None = None,
    spec_path: Path | str | None = None,
    mode: str | None = None,
    simulate_fixture: Path | None = None,
    no_discover: bool = False,
    force_discover: bool = False,
) -> dict:
    from datetime import date

    from nullone.mapping.spec_loader import default_spec_path
    from nullone.mapping.spec_resolver import is_cached_spec_valid, resolve_spec

    week = date.fromisoformat(week_ending) if week_ending else None
    if week is None and simulate_fixture is not None:
        from nullone.sources.parsers import parse_wngsr_json

        week = date.fromisoformat(parse_wngsr_json(simulate_fixture.read_bytes()).current_week)

    if (
        simulate_fixture is not None
        and week is not None
        and not force_discover
        and not no_discover
        and is_cached_spec_valid(default_spec_path(week.isoformat()), week)
    ):
        no_discover = True

    spec, path = await resolve_spec(
        week_ending=week,
        spec_path=Path(spec_path) if spec_path else None,
        force_discover=force_discover,
        no_discover=no_discover,
    )
    pipeline = ReleasePipeline(spec, mode_override=mode)
    result = await pipeline.run(simulate_fixture=simulate_fixture)
    result["spec_path"] = str(path)
    return result
