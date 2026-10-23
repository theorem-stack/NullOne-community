from __future__ import annotations

from nullone.config import PolymarketConfig
from nullone.exchanges.base import OrderRequest, OrderResult
from nullone.mapping.spec_loader import PolymarketBracket


class PolymarketOrderClient:
    def __init__(self, config: PolymarketConfig) -> None:
        self._config = config
        self._client = None

    def _build_client(self):
        from py_clob_client_v2 import ApiCreds, ClobClient

        creds = None
        if self._config.api_key and self._config.api_secret and self._config.api_passphrase:
            creds = ApiCreds(
                api_key=self._config.api_key,
                api_secret=self._config.api_secret,
                api_passphrase=self._config.api_passphrase,
            )

        kwargs: dict = {
            "host": self._config.host,
            "chain_id": self._config.chain_id,
            "key": self._config.private_key,
        }
        if creds:
            kwargs["creds"] = creds
        if self._config.funder:
            kwargs["funder"] = self._config.funder
            kwargs["signature_type"] = self._config.signature_type

        client = ClobClient(**kwargs)
        if creds is None:
            client.set_api_creds(client.create_or_derive_api_key())
        return client

    async def create_order(
        self,
        request: OrderRequest,
        bracket: PolymarketBracket,
        *,
        paper: bool = True,
    ) -> OrderResult:
        size = max(1.0, request.size_usd / max(request.price, 0.01))

        if paper:
            return OrderResult(
                submitted=False,
                fill_price=request.price,
                fill_size=size,
                order_id=None,
                paper=True,
            )

        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side

        client = self._client or self._build_client()
        self._client = client

        order_args = OrderArgs(
            token_id=bracket.token_id,
            price=request.price,
            side=Side.BUY,
            size=size,
        )
        options = PartialCreateOrderOptions(
            tick_size=bracket.tick_size,
            neg_risk=bracket.neg_risk,
        )
        resp = client.create_and_post_order(
            order_args=order_args,
            options=options,
            order_type=OrderType.FOK,
        )
        return OrderResult(
            submitted=True,
            fill_price=request.price,
            fill_size=size,
            order_id=str(resp.get("orderID") or resp.get("id", "")),
            paper=False,
        )

    async def get_fee_rate_bps(self, token_id: str) -> float:
        """Return taker fee as a fraction (e.g. 0.02 for 2%)."""
        if self._client is None:
            self._client = self._build_client()
        try:
            fee = self._client.get_fee_rate_bps(token_id)
            return float(fee) / 10_000.0
        except Exception:
            return 0.02
