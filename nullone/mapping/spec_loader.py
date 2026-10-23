from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from nullone.config import SPECS_DIR, load_settings


@dataclass(frozen=True)
class KalshiBracket:
    market_ticker: str


@dataclass(frozen=True)
class PolymarketBracket:
    token_id: str
    tick_size: str = "0.01"
    neg_risk: bool = False


@dataclass(frozen=True)
class BracketSpec:
    label: str
    min_bcf: int
    max_bcf: int
    kalshi: KalshiBracket | None
    polymarket: PolymarketBracket | None


@dataclass(frozen=True)
class ExecutionSpec:
    min_edge_after_fees: float
    max_entry_price: float
    release_capital_usd: float
    mode: str
    execution_strategy: str = "auto"
    parse_error_rate: float = 0.02
    min_leg_usd: float = 5.0
    max_legs: int = 6
    min_fill_prob: float = 0.3
    winner_yes_min_pct: float = 0.5
    max_leg_usd: float | None = None
    book_depth_usd: float = 100.0
    max_slippage_yes: float = 0.02
    absolute_floor_yes: float = 0.01
    force_winner_first: bool = True
    enable_loser_no: bool = True
    kalshi_fee_rate: float = 0.01


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    week_ending: str
    release_at_et: str | None
    metric: str
    region: str
    brackets: list[BracketSpec]
    execution: ExecutionSpec


def _parse_bracket(raw: dict) -> BracketSpec:
    kalshi_raw = raw.get("kalshi") or {}
    poly_raw = raw.get("polymarket") or {}

    kalshi = None
    if kalshi_raw.get("market_ticker") and kalshi_raw["market_ticker"] != "TBD":
        kalshi = KalshiBracket(market_ticker=kalshi_raw["market_ticker"])

    polymarket = None
    if poly_raw.get("token_id") and poly_raw["token_id"] != "TBD":
        polymarket = PolymarketBracket(
            token_id=str(poly_raw["token_id"]),
            tick_size=str(poly_raw.get("tick_size", "0.01")),
            neg_risk=bool(poly_raw.get("neg_risk", False)),
        )

    return BracketSpec(
        label=raw["label"],
        min_bcf=int(raw["min"]),
        max_bcf=int(raw["max"]),
        kalshi=kalshi,
        polymarket=polymarket,
    )


def _parse_execution(exec_raw: dict) -> ExecutionSpec:
    settings = load_settings()

    env_sets_release_capital = (
        "NULLONE_RELEASE_CAPITAL_USD" in os.environ
        or "NULLONE_ORDER_SIZE_USD" in os.environ
    )
    release_capital_usd = (
        settings.release_capital_usd
        if env_sets_release_capital
        else float(exec_raw.get("release_capital_usd", settings.release_capital_usd))
    )

    env_sets_strategy = "NULLONE_EXECUTION_STRATEGY" in os.environ
    execution_strategy = (
        settings.execution_strategy
        if env_sets_strategy
        else str(exec_raw.get("execution_strategy", settings.execution_strategy)).lower()
    )
    if not env_sets_strategy and bool(exec_raw.get("multi_leg", False)):
        execution_strategy = "auto"
    if execution_strategy not in {"single_leg", "auto"}:
        raise ValueError("execution_strategy must be one of: single_leg, auto")

    return ExecutionSpec(
        min_edge_after_fees=float(exec_raw.get("min_edge_after_fees", 0.05)),
        max_entry_price=float(exec_raw.get("max_entry_price", 0.92)),
        release_capital_usd=release_capital_usd,
        mode=str(exec_raw.get("mode", settings.mode)).lower(),
        execution_strategy=execution_strategy,
        parse_error_rate=float(exec_raw.get("parse_error_rate", settings.parse_error_rate)),
        min_leg_usd=float(exec_raw.get("min_leg_usd", settings.min_leg_usd)),
        max_legs=int(exec_raw.get("max_legs", settings.max_legs)),
        min_fill_prob=float(exec_raw.get("min_fill_prob", settings.min_fill_prob)),
        winner_yes_min_pct=float(
            exec_raw.get("winner_yes_min_pct", settings.winner_yes_min_pct)
        ),
        max_leg_usd=(
            float(exec_raw["max_leg_usd"])
            if exec_raw.get("max_leg_usd") not in (None, "")
            else settings.max_leg_usd
        ),
        book_depth_usd=float(exec_raw.get("book_depth_usd", settings.book_depth_usd)),
        max_slippage_yes=float(exec_raw.get("max_slippage_yes", settings.max_slippage_yes)),
        absolute_floor_yes=float(exec_raw.get("absolute_floor_yes", settings.absolute_floor_yes)),
        force_winner_first=bool(exec_raw.get("force_winner_first", settings.force_winner_first)),
        enable_loser_no=bool(exec_raw.get("enable_loser_no", settings.enable_loser_no)),
        kalshi_fee_rate=float(exec_raw.get("kalshi_fee_rate", settings.kalshi_fee_rate)),
    )


def load_spec(path: Path | str) -> EventSpec:
    spec_path = Path(path)
    with spec_path.open() as f:
        raw = yaml.safe_load(f)

    exec_raw = raw["execution"]
    execution = _parse_execution(exec_raw)

    return EventSpec(
        event_id=raw["event_id"],
        week_ending=raw["week_ending"],
        release_at_et=raw.get("release_at_et"),
        metric=raw.get("metric", "net_change_bcf"),
        region=raw.get("region", "lower48"),
        brackets=[_parse_bracket(b) for b in raw["brackets"]],
        execution=execution,
    )


def default_spec_path(week_ending: str) -> Path:
    return SPECS_DIR / f"eia_wngsr_{week_ending}.yaml"
