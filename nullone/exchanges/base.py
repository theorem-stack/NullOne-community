from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    best_ask: float | None
    best_bid: float | None
    asks: list[OrderBookLevel]
    bids: list[OrderBookLevel]


@dataclass(frozen=True)
class OrderResult:
    submitted: bool
    fill_price: float | None
    fill_size: float
    order_id: str | None
    error: str | None = None
    paper: bool = False
    remaining_count: float | None = None
    average_fee_paid: float | None = None
    ts_ms: int | None = None


@dataclass(frozen=True)
class BatchOrderResult:
    submitted: bool
    orders: list[OrderResult]
    error: str | None = None
    paper: bool = False


@dataclass(frozen=True)
class OrderRequest:
    market_id: str
    side: str
    price: float
    size_usd: float
    time_in_force: str = "immediate_or_cancel"
    count: float | None = None
