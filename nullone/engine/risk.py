from __future__ import annotations

from dataclasses import dataclass

from nullone.exchanges.base import OrderBookSnapshot
from nullone.mapping.spec_loader import BracketSpec, ExecutionSpec


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    max_price: float
    best_ask: float | None
    expected_edge: float | None
    reason: str


def compute_max_entry(
    execution: ExecutionSpec,
    *,
    taker_fee_rate: float,
) -> float:
    """Max bid such that 1.0 - price - fee >= min_edge."""
    return min(
        execution.max_entry_price,
        1.0 - execution.min_edge_after_fees - taker_fee_rate,
    )


def evaluate_trade(
    execution: ExecutionSpec,
    book: OrderBookSnapshot | None,
    *,
    taker_fee_rate: float = 0.0,
) -> RiskDecision:
    max_price = compute_max_entry(execution, taker_fee_rate=taker_fee_rate)
    best_ask = book.best_ask if book else None

    if best_ask is None:
        return RiskDecision(
            approved=False,
            max_price=max_price,
            best_ask=None,
            expected_edge=None,
            reason="no liquidity on book",
        )

    expected_edge = 1.0 - best_ask - taker_fee_rate
    if best_ask > max_price:
        return RiskDecision(
            approved=False,
            max_price=max_price,
            best_ask=best_ask,
            expected_edge=expected_edge,
            reason=f"ask {best_ask:.4f} above max {max_price:.4f}",
        )

    if expected_edge < execution.min_edge_after_fees:
        return RiskDecision(
            approved=False,
            max_price=max_price,
            best_ask=best_ask,
            expected_edge=expected_edge,
            reason=f"edge {expected_edge:.4f} below minimum",
        )

    return RiskDecision(
        approved=True,
        max_price=max_price,
        best_ask=best_ask,
        expected_edge=expected_edge,
        reason="approved",
    )


def order_price_for_bracket(
    decision: RiskDecision,
    bracket: BracketSpec,
) -> float:
    if decision.best_ask is None:
        raise ValueError("Cannot price order without best ask")
    return min(decision.max_price, decision.best_ask)
