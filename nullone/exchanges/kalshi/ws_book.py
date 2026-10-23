from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import websockets
import httpx
import orjson

from nullone.exchanges.base import OrderBookLevel, OrderBookSnapshot
from nullone.exchanges.kalshi.auth import auth_headers, load_private_key
from nullone.config import KalshiConfig

TRADE_API_PREFIX = "/trade-api/v2"


def _rest_sign_path(path: str) -> str:
    return path if path.startswith(TRADE_API_PREFIX) else f"{TRADE_API_PREFIX}{path}"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KalshiBookVector:
    market_ticker: str
    best_bid_yes: float | None
    best_bid_no: float | None
    best_ask_yes: float | None
    best_ask_no: float | None
    depth_yes_usd: float
    depth_no_usd: float


class KalshiOrderBookMirror:
    """Maintains a local YES-side order book from Kalshi orderbook_delta WS."""

    def __init__(self, config: KalshiConfig, tickers: list[str]) -> None:
        self._config = config
        self._tickers = tickers
        self._books: dict[str, dict[str, dict[float, float]]] = {
            t: {"yes": {}, "no": {}} for t in tickers
        }
        self._vectors: dict[str, KalshiBookVector] = {
            t: KalshiBookVector(t, None, None, None, None, 0.0, 0.0) for t in tickers
        }
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._private_key = load_private_key(
            path=config.private_key_path,
            pem=config.private_key_pem,
        )

    def snapshot(self, ticker: str) -> OrderBookSnapshot:
        yes_bids = self._books.get(ticker, {}).get("yes", {})
        no_bids = self._books.get(ticker, {}).get("no", {})

        bid_levels = [
            OrderBookLevel(price=p / 100.0 if p > 1 else p, size=s)
            for p, s in yes_bids.items()
            if s > 0
        ]
        bid_levels.sort(key=lambda x: x.price, reverse=True)

        # YES ask implied from NO bids: ask_yes = 1 - bid_no
        ask_levels = [
            OrderBookLevel(price=1.0 - (p / 100.0 if p > 1 else p), size=s)
            for p, s in no_bids.items()
            if s > 0
        ]
        ask_levels.sort(key=lambda x: x.price)

        return OrderBookSnapshot(
            best_bid=bid_levels[0].price if bid_levels else None,
            best_ask=ask_levels[0].price if ask_levels else None,
            bids=bid_levels,
            asks=ask_levels,
        )

    def vector(self, ticker: str, *, max_price: float = 0.99) -> KalshiBookVector:
        if ticker not in self._vectors:
            self._recompute_vector(ticker, max_price=max_price)
        return self._vectors.get(
            ticker,
            KalshiBookVector(ticker, None, None, None, None, 0.0, 0.0),
        )

    async def seed_from_rest(self, *, max_price: float = 0.99) -> None:
        """Seed books with REST snapshots before relying on WS deltas."""
        async with httpx.AsyncClient(base_url=self._config.base_url, timeout=10.0) as client:
            await asyncio.gather(
                *(self._seed_one(client, ticker, max_price=max_price) for ticker in self._tickers)
            )

    async def start(self) -> None:
        if not self._tickers:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        ws_path = "/trade-api/ws/v2"
        headers = auth_headers(
            self._private_key,
            self._config.api_key,
            method="GET",
            path=ws_path,
        )

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._config.ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                ) as ws:
                    subscribe = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": self._tickers,
                        },
                    }
                    await ws.send(orjson.dumps(subscribe).decode("utf-8"))

                    async for message in ws:
                        if self._stop.is_set():
                            break
                        self._handle_message(orjson.loads(message))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Kalshi WS disconnected: %s", exc)
                await asyncio.sleep(1.0)

    async def _seed_one(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        *,
        max_price: float,
    ) -> None:
        path = f"/markets/{ticker}/orderbook"
        headers = auth_headers(
            self._private_key,
            self._config.api_key,
            method="GET",
            path=_rest_sign_path(path),
        )
        resp = await client.get(path, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        orderbook = payload.get("orderbook") or payload.get("orderbook_fp") or payload
        self._books[ticker] = {
            "yes": self._levels_to_book(orderbook.get("yes") or orderbook.get("yes_dollars") or []),
            "no": self._levels_to_book(orderbook.get("no") or orderbook.get("no_dollars") or []),
        }
        self._recompute_vector(ticker, max_price=max_price)

    @staticmethod
    def _levels_to_book(levels: list) -> dict[float, float]:
        book: dict[float, float] = {}
        for level in levels:
            if len(level) < 2:
                continue
            price = float(level[0])
            size = float(level[1])
            if price > 1:
                price /= 100.0
            if size > 0:
                book[price] = size
        return book

    def _handle_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        if msg_type == "orderbook_snapshot":
            self._apply_snapshot(msg.get("msg", {}))
        elif msg_type == "orderbook_delta":
            self._apply_delta(msg.get("msg", {}))

    def _apply_snapshot(self, payload: dict[str, Any]) -> None:
        ticker = payload.get("market_ticker")
        if not ticker or ticker not in self._books:
            return
        self._books[ticker] = {"yes": {}, "no": {}}
        for side_key in ("yes", "no"):
            for level in payload.get(side_key, []):
                price = float(level[0])
                size = float(level[1])
                if size > 0:
                    self._books[ticker][side_key][price] = size
        self._recompute_vector(ticker)

    def _apply_delta(self, payload: dict[str, Any]) -> None:
        ticker = payload.get("market_ticker")
        if not ticker or ticker not in self._books:
            return
        side = payload.get("side", "yes")
        price = float(payload.get("price", 0))
        delta = float(payload.get("delta", 0))
        book = self._books[ticker][side]
        book[price] = book.get(price, 0.0) + delta
        if book[price] <= 0:
            book.pop(price, None)
        self._recompute_vector(ticker)

    def _recompute_vector(self, ticker: str, *, max_price: float = 0.99) -> None:
        book = self._books.get(ticker)
        if not book:
            return
        yes_bids = book.get("yes", {})
        no_bids = book.get("no", {})
        best_bid_yes = max((p / 100.0 if p > 1 else p for p, s in yes_bids.items() if s > 0), default=None)
        best_bid_no = max((p / 100.0 if p > 1 else p for p, s in no_bids.items() if s > 0), default=None)
        best_ask_yes = 1.0 - best_bid_no if best_bid_no is not None else None
        best_ask_no = 1.0 - best_bid_yes if best_bid_yes is not None else None

        depth_yes_usd = 0.0
        for price, size in no_bids.items():
            bid_no = price / 100.0 if price > 1 else price
            ask_yes = 1.0 - bid_no
            if ask_yes <= max_price:
                depth_yes_usd += ask_yes * size

        depth_no_usd = 0.0
        for price, size in yes_bids.items():
            bid_yes = price / 100.0 if price > 1 else price
            ask_no = 1.0 - bid_yes
            if ask_no <= max_price:
                depth_no_usd += ask_no * size

        self._vectors[ticker] = KalshiBookVector(
            market_ticker=ticker,
            best_bid_yes=best_bid_yes,
            best_bid_no=best_bid_no,
            best_ask_yes=best_ask_yes,
            best_ask_no=best_ask_no,
            depth_yes_usd=depth_yes_usd,
            depth_no_usd=depth_no_usd,
        )
