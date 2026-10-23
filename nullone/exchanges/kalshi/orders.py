from __future__ import annotations

import uuid

import httpx

from nullone.config import KalshiConfig
from nullone.exchanges.base import BatchOrderResult, OrderRequest, OrderResult
from nullone.exchanges.kalshi.auth import auth_headers, load_private_key


TRADE_API_PREFIX = "/trade-api/v2"


def trade_api_sign_path(path: str) -> str:
    return path if path.startswith(TRADE_API_PREFIX) else f"{TRADE_API_PREFIX}{path}"


def _float_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def build_batch_order_payload(requests: list[OrderRequest]) -> dict:
    orders = []
    for request in requests:
        count = request.count if request.count is not None else max(1, int(request.size_usd / max(request.price, 0.01)))
        orders.append(
            {
                "ticker": request.market_id,
                "client_order_id": str(uuid.uuid4()),
                "side": request.side,
                "count": f"{count:.2f}",
                "price": f"{request.price:.4f}",
                "time_in_force": request.time_in_force,
                "self_trade_prevention_type": "taker_at_cross",
            }
        )
    return {"orders": orders}


class KalshiOrderClient:
    def __init__(self, config: KalshiConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._private_key = load_private_key(
            path=config.private_key_path,
            pem=config.private_key_pem,
        )

    async def __aenter__(self) -> KalshiOrderClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=httpx.Timeout(5.0, connect=2.0),
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def create_order(
        self,
        request: OrderRequest,
        *,
        paper: bool = True,
    ) -> OrderResult:
        count = request.count if request.count is not None else max(1, int(request.size_usd / max(request.price, 0.01)))
        body = {
            "ticker": request.market_id,
            "client_order_id": str(uuid.uuid4()),
            "side": request.side,
            "count": f"{count:.2f}",
            "price": f"{request.price:.4f}",
            "time_in_force": request.time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }

        if paper:
            return OrderResult(
                submitted=False,
                fill_price=request.price,
                fill_size=float(count),
                order_id=None,
                paper=True,
            )

        assert self._client is not None
        path = "/portfolio/events/orders"
        headers = auth_headers(
            self._private_key,
            self._config.api_key,
            method="POST",
            path=trade_api_sign_path(path),
        )
        resp = await self._client.post(path, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        fill_count = float(data.get("fill_count", "0"))
        avg_price = data.get("average_fill_price")
        return OrderResult(
            submitted=True,
            fill_price=float(avg_price) if avg_price else request.price,
            fill_size=fill_count,
            order_id=data.get("order_id"),
            paper=False,
        )

    async def create_batch_orders(
        self,
        requests: list[OrderRequest],
        *,
        paper: bool = True,
    ) -> BatchOrderResult:
        orders = []
        payload = build_batch_order_payload(requests)
        for request in requests:
            count = request.count if request.count is not None else max(1, int(request.size_usd / max(request.price, 0.01)))
            orders.append(
                OrderResult(
                    submitted=not paper,
                    fill_price=request.price,
                    fill_size=float(count),
                    order_id=None,
                    paper=paper,
                )
            )

        if paper:
            return BatchOrderResult(submitted=False, orders=orders, paper=True)

        assert self._client is not None
        path = "/portfolio/events/orders/batched"
        headers = auth_headers(
            self._private_key,
            self._config.api_key,
            method="POST",
            path=trade_api_sign_path(path),
        )
        resp = await self._client.post(path, headers=headers, json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Kalshi batch order failed: status={exc.response.status_code} "
                f"body={exc.response.text[:500]}"
            ) from exc
        payload = resp.json()
        response_orders = payload.get("orders") or payload.get("results") or []
        parsed: list[OrderResult] = []
        for idx, request in enumerate(requests):
            data = response_orders[idx] if idx < len(response_orders) else {}
            fill_count = float(data.get("fill_count", "0") or 0)
            avg_price = data.get("average_fill_price")
            parsed.append(
                OrderResult(
                    submitted=True,
                    fill_price=float(avg_price) if avg_price else request.price,
                    fill_size=fill_count,
                    order_id=data.get("order_id"),
                    error=data.get("error"),
                    paper=False,
                    remaining_count=_float_or_none(data.get("remaining_count")),
                    average_fee_paid=_float_or_none(data.get("average_fee_paid")),
                    ts_ms=data.get("ts_ms"),
                )
            )
        return BatchOrderResult(submitted=True, orders=parsed, paper=False)
