from __future__ import annotations

import httpx

from nullone.config import PolymarketConfig
from nullone.exchanges.base import OrderBookLevel, OrderBookSnapshot


class PolymarketBookClient:
    def __init__(self, config: PolymarketConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> PolymarketBookClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.host,
                timeout=httpx.Timeout(5.0, connect=2.0),
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def get_book(self, token_id: str) -> OrderBookSnapshot:
        assert self._client is not None
        resp = await self._client.get("/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()

        asks = [
            OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in data.get("asks", [])
        ]
        bids = [
            OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in data.get("bids", [])
        ]
        asks.sort(key=lambda x: x.price)
        bids.sort(key=lambda x: x.price, reverse=True)

        return OrderBookSnapshot(
            best_ask=asks[0].price if asks else None,
            best_bid=bids[0].price if bids else None,
            asks=asks,
            bids=bids,
        )
