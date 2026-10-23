from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from nullone.config import load_settings
from nullone.mapping.spec_loader import (
    BracketSpec,
    EventSpec,
    ExecutionSpec,
    KalshiBracket,
    PolymarketBracket,
)
from nullone.mapping.validate import validate_spec_for_run


class RuntimeConfigError(ValueError):
    """Raised when a generated runtime config is invalid."""


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    engine: str
    strategy: dict[str, Any]
    event: dict[str, Any]
    markets: dict[str, Any]
    execution: dict[str, Any]
    placement: dict[str, Any]
    runtime: dict[str, Any]
    path: Path | None = None


def _required_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise RuntimeConfigError(f"runtime config requires object field '{key}'")
    return value


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError(f"runtime config requires non-empty string field '{key}'")
    return value


def _required_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise RuntimeConfigError(f"runtime config requires integer field '{key}'")
    return value


def _optional_mapping(raw: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeConfigError(f"field '{key}' must be an object when provided")
    return value


def _validate_iso_datetime(raw: dict[str, Any], key: str) -> None:
    value = _required_str(raw, key)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeConfigError(f"field '{key}' must be ISO-8601 datetime") from exc


def load_runtime_config(path: Path | str) -> RuntimeConfig:
    config_path = Path(path)
    try:
        raw = orjson.loads(config_path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise RuntimeConfigError(f"invalid runtime JSON: {exc}") from exc
    except OSError as exc:
        raise RuntimeConfigError(f"cannot read runtime config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeConfigError("runtime config root must be an object")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise RuntimeConfigError("runtime config schema_version must be 1")

    engine = _required_str(raw, "engine").lower()
    if engine != "nullone":
        raise RuntimeConfigError("runtime config engine must be 'nullone'")

    strategy = _required_mapping(raw, "strategy")
    event = _required_mapping(raw, "event")
    markets = _required_mapping(raw, "markets")
    execution = _required_mapping(raw, "execution")
    placement = _required_mapping(raw, "placement")
    runtime = _required_mapping(raw, "runtime")

    _required_str(strategy, "name")
    _required_str(strategy, "event_id")
    if strategy.get("release_at_et") is not None:
        _validate_iso_datetime(strategy, "release_at_et")
    _required_str(event, "source")
    _required_str(event, "week_ending")
    _required_str(placement, "provider")
    _required_str(placement, "region")
    _required_str(placement, "runtime")
    _required_str(runtime, "run_id")
    _validate_iso_datetime(runtime, "warmup_starts_at")
    _validate_iso_datetime(runtime, "profiler_blackout_at")
    _validate_iso_datetime(runtime, "execute_at")

    return RuntimeConfig(
        schema_version=schema_version,
        engine=engine,
        strategy=strategy,
        event=event,
        markets=markets,
        execution=execution,
        placement=placement,
        runtime=runtime,
        path=config_path,
    )


def runtime_config_to_spec(config: RuntimeConfig) -> EventSpec:
    settings = load_settings()

    strategy = config.strategy
    event = config.event
    execution = config.execution
    brackets_raw = config.markets.get("brackets")
    if not isinstance(brackets_raw, list) or not brackets_raw:
        raise RuntimeConfigError("runtime config requires markets.brackets[]")

    if _required_str(event, "source") != "eia_wngsr":
        raise RuntimeConfigError("runtime event.source must be 'eia_wngsr'")

    mode = str(execution.get("mode", strategy.get("mode", settings.mode))).lower()
    if mode not in {"paper", "live"}:
        raise RuntimeConfigError("execution.mode must be one of: paper, live")

    execution_strategy = str(
        execution.get("execution_strategy", settings.execution_strategy)
    ).lower()
    if execution_strategy not in {"single_leg", "auto"}:
        raise RuntimeConfigError("execution.execution_strategy must be one of: single_leg, auto")

    release_capital_usd = float(
        execution.get("release_capital_usd", settings.release_capital_usd)
    )
    if release_capital_usd <= 0:
        raise RuntimeConfigError("execution.release_capital_usd must be greater than 0")

    brackets: list[BracketSpec] = []
    for idx, raw_bracket in enumerate(brackets_raw):
        if not isinstance(raw_bracket, dict):
            raise RuntimeConfigError(f"markets.brackets[{idx}] must be an object")
        kalshi_raw = _optional_mapping(raw_bracket, "kalshi")
        polymarket_raw = _optional_mapping(raw_bracket, "polymarket")

        kalshi = None
        if kalshi_raw and kalshi_raw.get("market_ticker"):
            kalshi = KalshiBracket(market_ticker=str(kalshi_raw["market_ticker"]))

        polymarket = None
        if polymarket_raw and polymarket_raw.get("token_id"):
            polymarket = PolymarketBracket(
                token_id=str(polymarket_raw["token_id"]),
                tick_size=str(polymarket_raw.get("tick_size", "0.01")),
                neg_risk=bool(polymarket_raw.get("neg_risk", False)),
            )

        brackets.append(
            BracketSpec(
                label=_required_str(raw_bracket, "label"),
                min_bcf=_required_int(raw_bracket, "min"),
                max_bcf=_required_int(raw_bracket, "max"),
                kalshi=kalshi,
                polymarket=polymarket,
            )
        )

    spec = EventSpec(
        event_id=_required_str(strategy, "event_id"),
        week_ending=_required_str(event, "week_ending"),
        release_at_et=strategy.get("release_at_et"),
        metric=str(event.get("metric", "net_change_bcf")),
        region=str(event.get("region", "lower48")),
        brackets=brackets,
        execution=ExecutionSpec(
            min_edge_after_fees=float(execution.get("min_edge_after_fees", 0.05)),
            max_entry_price=float(execution.get("max_entry_price", 0.92)),
            release_capital_usd=release_capital_usd,
            mode=mode,
            execution_strategy=execution_strategy,
            parse_error_rate=float(execution.get("parse_error_rate", settings.parse_error_rate)),
            min_leg_usd=float(execution.get("min_leg_usd", settings.min_leg_usd)),
            max_legs=int(execution.get("max_legs", settings.max_legs)),
            min_fill_prob=float(execution.get("min_fill_prob", settings.min_fill_prob)),
            winner_yes_min_pct=float(
                execution.get("winner_yes_min_pct", settings.winner_yes_min_pct)
            ),
            max_leg_usd=(
                float(execution["max_leg_usd"])
                if execution.get("max_leg_usd") not in (None, "")
                else settings.max_leg_usd
            ),
            book_depth_usd=float(execution.get("book_depth_usd", settings.book_depth_usd)),
            max_slippage_yes=float(
                execution.get("max_slippage_yes", settings.max_slippage_yes)
            ),
            absolute_floor_yes=float(
                execution.get("absolute_floor_yes", settings.absolute_floor_yes)
            ),
            force_winner_first=bool(
                execution.get("force_winner_first", settings.force_winner_first)
            ),
            enable_loser_no=bool(execution.get("enable_loser_no", settings.enable_loser_no)),
            kalshi_fee_rate=float(execution.get("kalshi_fee_rate", settings.kalshi_fee_rate)),
        ),
    )
    validate_spec_for_run(spec, week_ending=spec.week_ending)
    return spec


def load_runtime_spec(path: Path | str) -> tuple[RuntimeConfig, EventSpec]:
    config = load_runtime_config(path)
    return config, runtime_config_to_spec(config)
