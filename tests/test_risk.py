from nullone.engine.risk import compute_max_entry, evaluate_trade
from nullone.exchanges.base import OrderBookLevel, OrderBookSnapshot
from nullone.mapping.spec_loader import ExecutionSpec


def _book(ask: float) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        best_ask=ask,
        best_bid=0.4,
        asks=[OrderBookLevel(price=ask, size=10)],
        bids=[OrderBookLevel(price=0.4, size=10)],
    )


def test_compute_max_entry():
    execution = ExecutionSpec(
        min_edge_after_fees=0.05,
        max_entry_price=0.92,
        release_capital_usd=100,
        mode="paper",
    )
    assert compute_max_entry(execution, taker_fee_rate=0.02) == 0.92


def test_compute_max_entry_capped():
    execution = ExecutionSpec(
        min_edge_after_fees=0.05,
        max_entry_price=0.90,
        release_capital_usd=100,
        mode="paper",
    )
    assert compute_max_entry(execution, taker_fee_rate=0.01) == 0.90


def test_reject_high_ask():
    execution = ExecutionSpec(
        min_edge_after_fees=0.05,
        max_entry_price=0.92,
        release_capital_usd=100,
        mode="paper",
    )
    decision = evaluate_trade(execution, _book(0.96), taker_fee_rate=0.01)
    assert decision.approved is False


def test_approve_good_ask():
    execution = ExecutionSpec(
        min_edge_after_fees=0.05,
        max_entry_price=0.92,
        release_capital_usd=100,
        mode="paper",
    )
    decision = evaluate_trade(execution, _book(0.85), taker_fee_rate=0.01)
    assert decision.approved is True
