from __future__ import annotations

from dataclasses import dataclass

from nullone.mapping.scenario_index import Scenario, ScenarioLeg
from nullone.mapping.spec_loader import ExecutionSpec


@dataclass(frozen=True)
class LegMarketVector:
    market_ticker: str
    best_ask_yes: float | None
    best_ask_no: float | None
    depth_yes_usd: float
    depth_no_usd: float


@dataclass(frozen=True)
class AllocatedLeg:
    role: str
    label: str
    market_ticker: str
    economic_side: str
    v2_side: str
    entry_price: float
    limit_price_yes: float
    target_no_price: float | None
    raw_edge: float
    payout_ratio: float
    parse_p: float
    parse_q: float
    kelly_f: float
    fill_prob: float
    weight: float
    notional_usd: float
    depth_to_max_usd: float


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _depth_cap(depth_usd: float, max_leg_usd: float | None, capital_usd: float) -> float:
    cap = max_leg_usd if max_leg_usd is not None else capital_usd
    if cap <= 0:
        return 0.0
    return _clip(depth_usd / cap, 0.0, 1.0)


def _leg_prices(
    leg: ScenarioLeg,
    vector: LegMarketVector,
    execution: ExecutionSpec,
) -> tuple[str, str, float | None, float | None, float | None]:
    if leg.role == "winner_yes":
        return "yes", "bid", vector.best_ask_yes, vector.best_ask_yes, None
    if leg.role == "loser_no":
        if not execution.enable_loser_no:
            return "no", "ask", None, None, None
        target_no = vector.best_ask_no
        if target_no is None:
            return "no", "ask", None, None, None
        yes_limit = max(
            1.0 - target_no - execution.max_slippage_yes,
            execution.absolute_floor_yes,
        )
        return "no", "ask", target_no, yes_limit, target_no
    raise ValueError(f"Unknown scenario leg role: {leg.role}")


def allocate_multi_leg(
    scenario: Scenario,
    vectors: dict[str, LegMarketVector],
    execution: ExecutionSpec,
) -> list[AllocatedLeg]:
    """Allocate release capital across approved scenario legs.

    Uses parse-error-aware binary Kelly, then normalizes approved legs to the
    configured release capital budget.
    """

    capital = execution.release_capital_usd
    parse_q = _clip(execution.parse_error_rate, 0.0, 0.99)
    parse_p = 1.0 - parse_q
    candidates: list[tuple[ScenarioLeg, LegMarketVector, dict[str, float | str | None]]] = []

    for leg in scenario.legs[: execution.max_legs]:
        vector = vectors.get(leg.market_ticker)
        if vector is None:
            continue
        economic_side, v2_side, entry_price, limit_price_yes, target_no_price = _leg_prices(
            leg, vector, execution
        )
        if entry_price is None or limit_price_yes is None or entry_price <= 0:
            continue

        raw_edge = 1.0 - entry_price - execution.kalshi_fee_rate
        if raw_edge < execution.min_edge_after_fees:
            continue

        depth_usd = vector.depth_yes_usd if economic_side == "yes" else vector.depth_no_usd
        contracts_needed = execution.min_leg_usd / max(entry_price, 0.01)
        available_contracts = depth_usd / max(entry_price, 0.01)
        fill_prob = _clip(available_contracts / max(contracts_needed, 1.0), 0.0, 1.0)
        if fill_prob < execution.min_fill_prob:
            continue

        payout_ratio = max((1.0 - entry_price) / entry_price, 1e-9)
        kelly_f = _clip(parse_p - (parse_q / payout_ratio), 0.0, 1.0)
        if kelly_f <= 0:
            continue

        depth_factor = _depth_cap(depth_usd, execution.max_leg_usd, capital)
        effective = kelly_f * fill_prob * depth_factor
        if effective <= 0:
            continue

        candidates.append(
            (
                leg,
                vector,
                {
                    "economic_side": economic_side,
                    "v2_side": v2_side,
                    "entry_price": entry_price,
                    "limit_price_yes": limit_price_yes,
                    "target_no_price": target_no_price,
                    "raw_edge": raw_edge,
                    "payout_ratio": payout_ratio,
                    "kelly_f": kelly_f,
                    "fill_prob": fill_prob,
                    "depth_usd": depth_usd,
                    "effective": effective,
                },
            )
        )

    if not candidates:
        return []

    if execution.force_winner_first:
        winner = next((item for item in candidates if item[0].role == "winner_yes"), None)
        if winner is not None:
            depth_usd = float(winner[2]["depth_usd"] or 0.0)
            if depth_usd >= capital:
                candidates = [winner]

    total_effective = sum(float(item[2]["effective"] or 0.0) for item in candidates)
    if total_effective <= 0:
        return []

    allocations: list[AllocatedLeg] = []
    for leg, _vector, data in candidates:
        weight = float(data["effective"] or 0.0) / total_effective
        notional = weight * capital
        depth_usd = float(data["depth_usd"] or 0.0)
        if execution.max_leg_usd is not None:
            notional = min(notional, execution.max_leg_usd)
        notional = min(notional, depth_usd)
        if notional < execution.min_leg_usd:
            continue
        allocations.append(
            AllocatedLeg(
                role=leg.role,
                label=leg.label,
                market_ticker=leg.market_ticker,
                economic_side=str(data["economic_side"]),
                v2_side=str(data["v2_side"]),
                entry_price=float(data["entry_price"] or 0.0),
                limit_price_yes=float(data["limit_price_yes"] or 0.0),
                target_no_price=(
                    float(data["target_no_price"])
                    if data["target_no_price"] is not None
                    else None
                ),
                raw_edge=float(data["raw_edge"] or 0.0),
                payout_ratio=float(data["payout_ratio"] or 0.0),
                parse_p=parse_p,
                parse_q=parse_q,
                kelly_f=float(data["kelly_f"] or 0.0),
                fill_prob=float(data["fill_prob"] or 0.0),
                weight=weight,
                notional_usd=notional,
                depth_to_max_usd=depth_usd,
            )
        )
    return allocations

