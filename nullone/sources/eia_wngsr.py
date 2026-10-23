from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from nullone.config import WNGSR_CSV_URL, WNGSR_JSON_URL
from nullone.sources.csv_layout import WNGSRCsvLayout, validate_csv_layout
from nullone.sources.fast_csv import parse_wngsr_csv_fast, parse_wngsr_csv_streaming
from nullone.sources.parsers import WNGSRReading, parse_wngsr_json


@dataclass(frozen=True)
class PollResult:
    reading: WNGSRReading
    fetch_ms: float
    parse_ms: float
    source: str
    used_csv_fallback: bool = False


@dataclass
class WNGSRPoller:
    client: httpx.AsyncClient | None = None
    json_url: str = WNGSR_JSON_URL
    csv_url: str = WNGSR_CSV_URL
    csv_layout: WNGSRCsvLayout | None = field(default=None, init=False)
    _owns_client: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self._owns_client = self.client is None

    async def __aenter__(self) -> WNGSRPoller:
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(2.0, connect=1.0),
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self.client is not None:
            await self.client.close()

    async def validate_csv_layout(self) -> WNGSRCsvLayout:
        if self.csv_layout is not None:
            return self.csv_layout
        assert self.client is not None
        resp = await self.client.get(self.csv_url, follow_redirects=True)
        resp.raise_for_status()
        self.csv_layout = validate_csv_layout(resp.content)
        return self.csv_layout

    async def fetch_once(self) -> PollResult:
        assert self.client is not None
        layout = self.csv_layout

        async def _fetch_json() -> PollResult:
            t0 = time.perf_counter()
            resp = await self.client.get(self.json_url, follow_redirects=True)
            resp.raise_for_status()
            fetch_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            reading = parse_wngsr_json(resp.content)
            parse_ms = (time.perf_counter() - t1) * 1000
            return PollResult(reading=reading, fetch_ms=fetch_ms, parse_ms=parse_ms, source="json")

        async def _fetch_csv() -> PollResult:
            t0 = time.perf_counter()
            resp = await self.client.get(self.csv_url, follow_redirects=True)
            resp.raise_for_status()
            fetch_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            if layout is None:
                parsed = parse_wngsr_csv_fast(resp.content)
                self.csv_layout = parsed.layout
                reading = parsed.reading
                used_fallback = parsed.used_fallback
            else:
                reading = parse_wngsr_csv_streaming(resp.content, layout=layout)
                used_fallback = False
            parse_ms = (time.perf_counter() - t1) * 1000
            return PollResult(
                reading=reading,
                fetch_ms=fetch_ms,
                parse_ms=parse_ms,
                source="csv",
                used_csv_fallback=used_fallback,
            )

        json_task = asyncio.create_task(_fetch_json())
        csv_task = asyncio.create_task(_fetch_csv())
        json_result, csv_result = await asyncio.gather(json_task, csv_task, return_exceptions=True)

        if not isinstance(json_result, Exception):
            return json_result

        if not isinstance(csv_result, Exception):
            return csv_result

        raise csv_result

    async def fetch_csv_stream_once(self) -> PollResult:
        """Stream CSV bytes and parse as soon as the Total row arrives."""
        assert self.client is not None
        layout = self.csv_layout or await self.validate_csv_layout()
        t0 = time.perf_counter()
        buffer = bytearray()

        async with self.client.stream("GET", self.csv_url, follow_redirects=True) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                buffer.extend(chunk)
                if buffer.count(10) >= layout.total_line_no:
                    break

        fetch_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        reading = parse_wngsr_csv_streaming(bytes(buffer), layout=layout)
        parse_ms = (time.perf_counter() - t1) * 1000
        return PollResult(
            reading=reading,
            fetch_ms=fetch_ms,
            parse_ms=parse_ms,
            source="csv-stream",
        )

    async def wait_for_release(
        self,
        *,
        expected_week: str,
        baseline_net_change: int | None = None,
        poll_interval_s: float = 0.02,
        pre_poll_lead_s: float = 15.0,
        release_at_monotonic: float | None = None,
    ) -> PollResult:
        """Poll until a fresh print for expected_week is detected."""
        await self.validate_csv_layout()

        if release_at_monotonic is not None:
            while time.monotonic() < release_at_monotonic - pre_poll_lead_s:
                await asyncio.sleep(0.25)

        while True:
            json_task = asyncio.create_task(self.fetch_once())
            csv_stream_task = asyncio.create_task(self.fetch_csv_stream_once())
            done, pending = await asyncio.wait(
                {json_task, csv_stream_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            result: PollResult | None = None
            for task in done:
                try:
                    candidate = task.result()
                except Exception:
                    continue
                if self._is_fresh_print(
                    candidate,
                    expected_week=expected_week,
                    baseline_net_change=baseline_net_change,
                ):
                    result = candidate
                    break

            if result is not None:
                return result

            await asyncio.sleep(poll_interval_s)

    @staticmethod
    def _is_fresh_print(
        candidate: PollResult,
        *,
        expected_week: str,
        baseline_net_change: int | None,
    ) -> bool:
        reading = candidate.reading
        if reading.revision_flag:
            return False
        if baseline_net_change is not None and reading.net_change_bcf == baseline_net_change:
            return False
        if candidate.source == "json":
            return reading.current_week == expected_week
        # CSV has no week field — accept changed net change during release window.
        return True
