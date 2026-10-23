from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nullone.engine.allocation import LegMarketVector, allocate_multi_leg
from nullone.exchanges.base import OrderRequest
from nullone.exchanges.kalshi.orders import KalshiOrderClient, build_batch_order_payload
from nullone.exchanges.kalshi.ws_book import KalshiBookVector
from nullone.tools.kalshi_batch_probe import build_no_fill_probe_orders
from nullone.mapping.scenario_index import ScenarioIndex
from nullone.mapping.spec_loader import ExecutionSpec, load_spec


def test_scenario_index_matches_bracket_boundaries():
    spec = load_spec(Path("specs/eia_wngsr_2026-05-15.yaml"))
    index = ScenarioIndex(spec)

    assert index.scenario_for_net_change(96).winner_label == "96-100"
    assert index.scenario_for_net_change(100).winner_label == "96-100"
    assert index.scenario_for_net_change(101).winner_label == "101-105"
    assert index.scenario_for_net_change(105).winner_label == "101-105"
    assert index.scenario_for_net_change(106).winner_label == "106-110"
    assert index.scenario_for_net_change(110).winner_label == "106-110"


def test_allocate_multi_leg_uses_winner_yes_and_loser_no():
    spec = load_spec(Path("specs/eia_wngsr_2026-05-15.yaml"))
    scenario = ScenarioIndex(spec).scenario_for_net_change(101)
    execution = ExecutionSpec(
        min_edge_after_fees=0.02,
        max_entry_price=0.95,
        release_capital_usd=30,
        mode="paper",
        execution_strategy="auto",
        min_leg_usd=1,
        min_fill_prob=0,
        force_winner_first=False,
        parse_error_rate=0.01,
    )
    vectors = {
        "KXGASSTORAGE-MAY15-103": LegMarketVector(
            "KXGASSTORAGE-MAY15-103",
            best_ask_yes=0.82,
            best_ask_no=0.70,
            depth_yes_usd=20,
            depth_no_usd=20,
        ),
        "KXGASSTORAGE-MAY15-98": LegMarketVector(
            "KXGASSTORAGE-MAY15-98",
            best_ask_yes=0.50,
            best_ask_no=0.80,
            depth_yes_usd=10,
            depth_no_usd=10,
        ),
        "KXGASSTORAGE-MAY15-108": LegMarketVector(
            "KXGASSTORAGE-MAY15-108",
            best_ask_yes=0.50,
            best_ask_no=0.80,
            depth_yes_usd=10,
            depth_no_usd=10,
        ),
    }

    legs = allocate_multi_leg(scenario, vectors, execution)

    assert {leg.role for leg in legs} == {"winner_yes", "loser_no"}
    assert any(leg.v2_side == "bid" and leg.economic_side == "yes" for leg in legs)
    assert any(leg.v2_side == "ask" and leg.economic_side == "no" for leg in legs)
    assert sum(leg.notional_usd for leg in legs) <= execution.release_capital_usd


def test_build_batch_order_payload_is_readonly_shape():
    payload = build_batch_order_payload(
        [
            OrderRequest("TICKER-A", side="bid", price=0.8, size_usd=3),
            OrderRequest("TICKER-B", side="ask", price=0.2, size_usd=5),
        ]
    )

    assert list(payload) == ["orders"]
    assert payload["orders"][0]["side"] == "bid"
    assert payload["orders"][1]["side"] == "ask"
    assert payload["orders"][0]["price"] == "0.8000"
    assert payload["orders"][1]["price"] == "0.2000"


def test_build_batch_order_payload_honors_explicit_count():
    payload = build_batch_order_payload(
        [
            OrderRequest("TICKER-B", side="ask", price=0.04, size_usd=4, count=4.25),
        ]
    )

    assert payload["orders"][0]["count"] == "4.25"


def test_no_fill_probe_builds_non_crossing_bid_and_ask():
    vector = KalshiBookVector(
        market_ticker="TEST",
        best_bid_yes=0.4,
        best_bid_no=0.5,
        best_ask_yes=0.5,
        best_ask_no=0.6,
        depth_yes_usd=10,
        depth_no_usd=10,
    )

    orders = build_no_fill_probe_orders(vector, bid_price=0.01, ask_price=0.99)

    assert [order.side for order in orders] == ["bid", "ask"]
    assert orders[0].price == 0.01
    assert orders[1].price == 0.99


def test_no_fill_probe_rejects_crossing_bid():
    vector = KalshiBookVector(
        market_ticker="TEST",
        best_bid_yes=0.4,
        best_bid_no=0.5,
        best_ask_yes=0.5,
        best_ask_no=0.6,
        depth_yes_usd=10,
        depth_no_usd=10,
    )

    with pytest.raises(ValueError, match="Unsafe bid probe"):
        build_no_fill_probe_orders(vector, bid_price=0.5, include_ask=False)


def test_no_fill_probe_rejects_crossing_ask():
    vector = KalshiBookVector(
        market_ticker="TEST",
        best_bid_yes=0.99,
        best_bid_no=0.5,
        best_ask_yes=0.5,
        best_ask_no=0.01,
        depth_yes_usd=10,
        depth_no_usd=10,
    )

    with pytest.raises(ValueError, match="Unsafe ask probe"):
        build_no_fill_probe_orders(vector, ask_price=0.99, include_bid=False)


@pytest.mark.asyncio
async def test_kalshi_batch_orders_paper(monkeypatch):
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.orders.load_private_key",
        lambda **kwargs: MagicMock(),
    )
    config = MagicMock()
    config.private_key_path = "/dev/null"
    config.private_key_pem = None
    config.base_url = "https://example.test"
    client = KalshiOrderClient(config)

    batch = await client.create_batch_orders(
        [
            OrderRequest("TICKER-A", side="bid", price=0.8, size_usd=10),
            OrderRequest("TICKER-B", side="ask", price=0.2, size_usd=10),
        ],
        paper=True,
    )

    assert batch.paper is True
    assert len(batch.orders) == 2
    assert batch.orders[0].fill_price == 0.8
    assert batch.orders[1].fill_price == 0.2


@pytest.mark.asyncio
async def test_kalshi_batch_orders_posts_v2_payload(monkeypatch):
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.orders.load_private_key",
        lambda **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "nullone.exchanges.kalshi.orders.auth_headers",
        lambda *args, **kwargs: {"Authorization": "test"},
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "orders": [
                    {
                        "fill_count": "3.00",
                        "remaining_count": "0.00",
                        "average_fill_price": "0.80",
                        "average_fee_paid": "0.01",
                        "order_id": "yes-1",
                        "ts_ms": 123,
                    },
                    {
                        "fill_count": "5.00",
                        "remaining_count": "1.00",
                        "average_fill_price": "0.20",
                        "average_fee_paid": "0.02",
                        "order_id": "no-1",
                        "ts_ms": 124,
                    },
                ]
            }

    class FakeClient:
        def __init__(self):
            self.path = None
            self.headers = None
            self.json_body = None

        async def post(self, path, *, headers, json):
            self.path = path
            self.headers = headers
            self.json_body = json
            return FakeResponse()

    config = MagicMock()
    config.private_key_path = "/dev/null"
    config.private_key_pem = None
    config.api_key = "test"
    config.base_url = "https://example.test"
    fake_client = FakeClient()
    client = KalshiOrderClient(config, fake_client)

    batch = await client.create_batch_orders(
        [
            OrderRequest("TICKER-A", side="bid", price=0.8, size_usd=3),
            OrderRequest("TICKER-B", side="ask", price=0.2, size_usd=5),
        ],
        paper=False,
    )

    assert fake_client.path == "/portfolio/events/orders/batched"
    assert fake_client.json_body["orders"][0]["side"] == "bid"
    assert fake_client.json_body["orders"][1]["side"] == "ask"
    assert fake_client.json_body["orders"][0]["price"] == "0.8000"
    assert fake_client.json_body["orders"][1]["price"] == "0.2000"
    assert batch.submitted is True
    assert batch.orders[0].order_id == "yes-1"
    assert batch.orders[1].order_id == "no-1"
    assert batch.orders[0].remaining_count == 0.0
    assert batch.orders[1].remaining_count == 1.0
    assert batch.orders[0].average_fee_paid == 0.01
    assert batch.orders[1].ts_ms == 124

