from __future__ import annotations

import re
from typing import Any

import httpx

from nullone.config import PolymarketConfig


class GammaClient:
    def __init__(self, config: PolymarketConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> GammaClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def search_markets(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        assert self._client is not None
        resp = await self._client.get(
            f"{self._config.gamma_url}/public-search",
            params={"q": query, "limit_per_type": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", []) or data.get("markets", []) or []

    async def find_storage_markets(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for query in ("EIA natural gas storage", "natural gas storage Bcf", "weekly storage"):
            results.extend(await self.search_markets(query))
        return results


def parse_bracket_from_title(title: str) -> tuple[int, int] | None:
    """Extract min/max Bcf from titles like '85-89 Bcf' or '85 to 89 Bcf'."""
    match = re.search(r"(-?\d+)\s*(?:-|to)\s*(-?\d+)\s*bcf", title, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
