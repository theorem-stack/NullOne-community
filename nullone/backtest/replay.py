from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import orjson

from nullone.engine.risk import evaluate_trade
from nullone.mapping.bracket import BracketMapper
from nullone.mapping.spec_loader import EventSpec, load_spec
from nullone.sources.parsers import parse_wngsr_json


@dataclass(frozen=True)
class ReplayResult:
    event_id: str
    net_change_bcf: int
    winning_bracket: str
    kalshi_would_trade: bool
    polymarket_would_trade: bool
    kalshi_ask: float | None
    polymarket_ask: float | None


def replay_snapshot(
    spec: EventSpec,
    wngsr_path: Path,
    *,
    kalshi_ask: float | None = None,
    polymarket_ask: float | None = None,
    kalshi_fee: float = 0.01,
    poly_fee: float = 0.02,
) -> ReplayResult:
    raw = wngsr_path.read_bytes()
    reading = parse_wngsr_json(raw)
    mapper = BracketMapper(spec)
    winning = mapper.map_net_change(reading.net_change_bcf)

    from nullone.exchanges.base import OrderBookSnapshot, OrderBookLevel

    def _book(ask: float | None) -> OrderBookSnapshot | None:
        if ask is None:
            return None
        return OrderBookSnapshot(
            best_ask=ask,
            best_bid=None,
            asks=[OrderBookLevel(price=ask, size=100)],
            bids=[],
        )

    k_decision = evaluate_trade(spec.execution, _book(kalshi_ask), taker_fee_rate=kalshi_fee)
    p_decision = evaluate_trade(spec.execution, _book(polymarket_ask), taker_fee_rate=poly_fee)

    return ReplayResult(
        event_id=spec.event_id,
        net_change_bcf=reading.net_change_bcf,
        winning_bracket=winning.label,
        kalshi_would_trade=k_decision.approved,
        polymarket_would_trade=p_decision.approved,
        kalshi_ask=kalshi_ask,
        polymarket_ask=polymarket_ask,
    )


def replay_batch(
    spec_path: Path,
    snapshots_dir: Path,
    *,
    default_ask: float = 0.85,
) -> list[dict]:
    spec = load_spec(spec_path)
    results = []
    for path in sorted(snapshots_dir.glob("*.json")):
        try:
            replay = replay_snapshot(spec, path, kalshi_ask=default_ask, polymarket_ask=default_ask)
        except ValueError as exc:
            results.append({"file": path.name, "error": str(exc)})
            continue
        payload = replay.__dict__
        payload["file"] = path.name
        results.append(payload)
    return results


def write_replay_report(spec_path: Path, snapshots_dir: Path, output: Path) -> None:
    report = replay_batch(spec_path, snapshots_dir)
    output.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n")
